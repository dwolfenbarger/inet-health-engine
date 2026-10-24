BEGIN;
CREATE SCHEMA IF NOT EXISTS inet_catalog;
CREATE TABLE IF NOT EXISTS inet_catalog.generations(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, captured_at timestamptz NOT NULL DEFAULT now(),
 published_at timestamptz, observers jsonb NOT NULL DEFAULT '[]', summary jsonb NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS inet_catalog.routes(
 generation bigint NOT NULL, observer text NOT NULL, prefix cidr NOT NULL,
 origin_asn bigint, peer_asn bigint NOT NULL, collector text NOT NULL,
 transit bigint[] NOT NULL, members bigint[] NOT NULL, family smallint NOT NULL,
 event_at timestamptz NOT NULL, path jsonb NOT NULL, attrs jsonb NOT NULL,
 evidence_id text NOT NULL, rank smallint NOT NULL,
 PRIMARY KEY(generation,observer,prefix)) PARTITION BY LIST(generation);
CREATE INDEX IF NOT EXISTS catalog_routes_origin ON inet_catalog.routes(generation,origin_asn,observer,prefix);
CREATE INDEX IF NOT EXISTS catalog_routes_peer ON inet_catalog.routes(generation,peer_asn,observer,prefix);
CREATE INDEX IF NOT EXISTS catalog_routes_members ON inet_catalog.routes USING gin(members);
CREATE INDEX IF NOT EXISTS catalog_routes_transit ON inet_catalog.routes USING gin(transit);
CREATE INDEX IF NOT EXISTS catalog_routes_prefix ON inet_catalog.routes USING gist(prefix inet_ops);
CREATE TABLE IF NOT EXISTS inet_catalog.asns(
 generation bigint NOT NULL, asn bigint NOT NULL, routes bigint NOT NULL, prefixes bigint NOT NULL,
 origins bigint NOT NULL, transits bigint NOT NULL, peers bigint NOT NULL,
 ipv4 bigint NOT NULL, ipv6 bigint NOT NULL, observers bigint NOT NULL,
 PRIMARY KEY(generation,asn));
GRANT USAGE ON SCHEMA inet_catalog TO inetuser;
GRANT SELECT ON ALL TABLES IN SCHEMA inet_catalog TO inetuser;
COMMIT;
