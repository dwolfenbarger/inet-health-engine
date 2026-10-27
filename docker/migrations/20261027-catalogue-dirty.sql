-- Additive capture for incremental catalogue qualification. Apply only in DEV
-- until correctness, write amplification and serving retention are qualified.
BEGIN;
CREATE SCHEMA IF NOT EXISTS inet_incremental;
CREATE TABLE IF NOT EXISTS inet_incremental.dirty (
 observer text NOT NULL, prefix cidr NOT NULL, queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(observer,prefix));
CREATE TABLE IF NOT EXISTS inet_incremental.resets (
 observer text PRIMARY KEY, revision bigint NOT NULL DEFAULT 1,
 queued_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE OR REPLACE FUNCTION inet_incremental.capture_route() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND OLD IS NOT DISTINCT FROM NEW THEN RETURN NULL; END IF;
 IF TG_OP='DELETE' THEN
  INSERT INTO inet_incremental.dirty(observer,prefix) VALUES(OLD.observer,OLD.prefix) ON CONFLICT DO NOTHING;
 ELSE
  INSERT INTO inet_incremental.dirty(observer,prefix) VALUES(NEW.observer,NEW.prefix) ON CONFLICT DO NOTHING;
 END IF;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION inet_incremental.capture_reset() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (OLD.epoch,OLD.generation,OLD.peer_asn,OLD.collector) IS DISTINCT FROM
    (NEW.epoch,NEW.generation,NEW.peer_asn,NEW.collector) THEN
  INSERT INTO inet_incremental.resets(observer) VALUES(NEW.id)
  ON CONFLICT(observer) DO UPDATE SET revision=inet_incremental.resets.revision+1,queued_at=excluded.queued_at;
 END IF;
 RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS inet_catalogue_route_dirty ON routing_routes;
CREATE TRIGGER inet_catalogue_route_dirty AFTER INSERT OR UPDATE OR DELETE ON routing_routes
 FOR EACH ROW EXECUTE FUNCTION inet_incremental.capture_route();
DROP TRIGGER IF EXISTS inet_catalogue_observer_reset ON routing_observers;
CREATE TRIGGER inet_catalogue_observer_reset AFTER UPDATE ON routing_observers
 FOR EACH ROW EXECUTE FUNCTION inet_incremental.capture_reset();
CREATE OR REPLACE FUNCTION inet_incremental.capture_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO inet_incremental.resets(observer) SELECT id FROM routing_observers WHERE true
 ON CONFLICT(observer) DO UPDATE SET revision=inet_incremental.resets.revision+1,queued_at=excluded.queued_at;
 RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS inet_catalogue_source_truncate ON routing_routes;
CREATE TRIGGER inet_catalogue_source_truncate AFTER TRUNCATE ON routing_routes
 FOR EACH STATEMENT EXECUTE FUNCTION inet_incremental.capture_truncate();
GRANT USAGE ON SCHEMA inet_incremental TO inetuser;
GRANT SELECT,INSERT ON inet_incremental.dirty TO inetuser;
GRANT SELECT,INSERT,UPDATE ON inet_incremental.resets TO inetuser;
COMMIT;
