BEGIN;
SET LOCAL lock_timeout='3s';
CREATE TABLE IF NOT EXISTS routing_diagnostic_policy (
 id boolean PRIMARY KEY DEFAULT true CHECK(id), enabled boolean NOT NULL DEFAULT false,
 retention_hours integer NOT NULL DEFAULT 24 CHECK(retention_hours BETWEEN 1 AND 168),
 max_bytes bigint NOT NULL DEFAULT 8589934592, root_reserve bigint NOT NULL DEFAULT 37580963840,
 state text NOT NULL DEFAULT 'disabled', checked_at timestamptz, measured_bytes bigint
);
INSERT INTO routing_diagnostic_policy(id) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS routing_diagnostic_cursors (
 observer text PRIMARY KEY REFERENCES routing_observers(id), sequence bigint NOT NULL,
 epoch bigint NOT NULL DEFAULT 1, started_at timestamptz NOT NULL DEFAULT now(),
 received_at timestamptz NOT NULL, event_at timestamptz,
 gap_seen timestamptz NOT NULL, heartbeat_at timestamptz,
 status text NOT NULL DEFAULT 'warming', skipped_late bigint NOT NULL DEFAULT 0,
 processed bigint NOT NULL DEFAULT 0, error_code text
);
ALTER TABLE routing_diagnostic_cursors ADD COLUMN IF NOT EXISTS continuity jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS routing_diagnostic_state (
 observer text NOT NULL REFERENCES routing_observers(id), prefix cidr NOT NULL,
 observed_at timestamptz NOT NULL, state jsonb NOT NULL,
 PRIMARY KEY(observer,prefix)
);
CREATE INDEX IF NOT EXISTS diagnostic_state_expiry ON routing_diagnostic_state(observed_at);
CREATE TABLE IF NOT EXISTS routing_diagnostic_origins (
 observer text NOT NULL REFERENCES routing_observers(id), asn bigint NOT NULL,
 observed_at timestamptz NOT NULL, state jsonb NOT NULL, PRIMARY KEY(observer,asn)
);
CREATE TABLE IF NOT EXISTS routing_diagnostic_events (
 id bigserial PRIMARY KEY, event_id uuid NOT NULL UNIQUE,
 observer text NOT NULL REFERENCES routing_observers(id), sequence bigint NOT NULL,
 ordinal integer NOT NULL, epoch bigint NOT NULL, event_at timestamptz NOT NULL,
 received_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 prefix cidr, kind text NOT NULL, candidate text, origin_asn bigint, expected_asn bigint,
 involved_asns bigint[] NOT NULL DEFAULT '{}', evidence jsonb NOT NULL,
 UNIQUE(observer,sequence,ordinal,kind)
);
CREATE INDEX IF NOT EXISTS diagnostic_events_prefix ON routing_diagnostic_events(prefix,event_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS diagnostic_events_time ON routing_diagnostic_events(event_at DESC);
CREATE INDEX IF NOT EXISTS diagnostic_events_candidate ON routing_diagnostic_events(event_at DESC) WHERE candidate IS NOT NULL;
CREATE INDEX IF NOT EXISTS diagnostic_events_asns ON routing_diagnostic_events USING gin(involved_asns);
GRANT SELECT ON routing_diagnostic_policy TO inetuser;
GRANT SELECT,INSERT,UPDATE,DELETE ON routing_diagnostic_cursors,routing_diagnostic_state,routing_diagnostic_origins,routing_diagnostic_events TO inetuser;
GRANT UPDATE(state,checked_at,measured_bytes) ON routing_diagnostic_policy TO inetuser;
GRANT USAGE,SELECT ON SEQUENCE routing_diagnostic_events_id_seq TO inetuser;
COMMIT;
