-- TimescaleDB initialization for inet-health-engine
-- All 7 hypertables. network_events and as_health were missing on initial VENGEANCE deploy
-- due to a syntax error in the original (missing comma after PRIMARY KEY clause).
-- Fixed 2026-04-27.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- BGP UPDATES - raw update stream
CREATE TABLE IF NOT EXISTS bgp_updates (
    time            TIMESTAMPTZ NOT NULL,
    prefix          TEXT NOT NULL,
    origin_asn      BIGINT,
    as_path         BIGINT[],
    communities     TEXT[],
    change_type     TEXT,
    collector       TEXT,
    peer_asn        BIGINT,
    next_hop        INET,
    next_hop_raw    TEXT,
    rpki_status     TEXT
);
SELECT create_hypertable('bgp_updates', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS bgp_updates_prefix_idx ON bgp_updates (prefix, time DESC);
CREATE INDEX IF NOT EXISTS bgp_updates_origin_idx ON bgp_updates (origin_asn, time DESC);

-- BGP ANOMALIES - detected events
CREATE TABLE IF NOT EXISTS bgp_anomalies (
    time            TIMESTAMPTZ NOT NULL,
    event_id        UUID DEFAULT gen_random_uuid(),
    event_type      TEXT NOT NULL,
    affected_prefix TEXT,
    origin_asn      BIGINT,
    expected_asn    BIGINT,
    severity        SMALLINT,
    confidence      REAL,
    source          TEXT,
    raw_data        JSONB
);
SELECT create_hypertable('bgp_anomalies', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS bgp_anomalies_prefix_idx ON bgp_anomalies (affected_prefix, time DESC);
CREATE INDEX IF NOT EXISTS bgp_anomalies_origin_idx ON bgp_anomalies (origin_asn, time DESC);

-- TRAFFIC METRICS - regional health signals
CREATE TABLE IF NOT EXISTS traffic_metrics (
    time            TIMESTAMPTZ NOT NULL,
    region          TEXT,
    country_code    TEXT,
    asn             BIGINT,
    metric_type     TEXT,
    value           DOUBLE PRECISION,
    source          TEXT
);
SELECT create_hypertable('traffic_metrics', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS traffic_metrics_region_idx ON traffic_metrics (region, time DESC);
CREATE INDEX IF NOT EXISTS traffic_metrics_asn_idx    ON traffic_metrics (asn, time DESC);

-- NETWORK EVENTS - correlated incidents (Radar + IODA + RIS)
-- NOTE: no PRIMARY KEY constraint - TimescaleDB hypertables cannot have
-- arbitrary PKs on non-partitioning columns. Use event_id + time for dedup.
CREATE TABLE IF NOT EXISTS network_events (
    time                    TIMESTAMPTZ NOT NULL,
    event_id                UUID DEFAULT gen_random_uuid(),
    event_type              TEXT NOT NULL,
    severity                SMALLINT,
    confidence              REAL,
    affected_asns           BIGINT[],
    affected_prefixes       TEXT[],
    affected_regions        TEXT[],
    tech_signal_count       INTEGER DEFAULT 0,
    community_signal_count  INTEGER DEFAULT 0,
    correlation_score       REAL,
    source_count            INTEGER DEFAULT 1,
    first_detected          TIMESTAMPTZ NOT NULL,
    last_updated            TIMESTAMPTZ,
    resolved_at             TIMESTAMPTZ,
    summary                 TEXT,
    raw_signals             JSONB
);
SELECT create_hypertable('network_events', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS network_events_type_idx ON network_events (event_type, time DESC);
CREATE INDEX IF NOT EXISTS network_events_asns_idx ON network_events USING GIN (affected_asns);

-- RPKI STATUS - validation results per prefix/asn
CREATE TABLE IF NOT EXISTS rpki_status (
    time            TIMESTAMPTZ NOT NULL,
    prefix          TEXT NOT NULL,
    origin_asn      BIGINT,
    status          TEXT,
    max_length      INTEGER,
    roa_count       INTEGER DEFAULT 0,
    validator       TEXT,
    raw_response    JSONB
);
SELECT create_hypertable('rpki_status', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS rpki_status_prefix_idx ON rpki_status (prefix, time DESC);

-- ATLAS MEASUREMENTS - RIPE Atlas probe results
CREATE TABLE IF NOT EXISTS atlas_measurements (
    time            TIMESTAMPTZ NOT NULL,
    measurement_id  INTEGER,
    probe_id        INTEGER,
    target          TEXT,
    target_ip       INET,
    measurement_type TEXT,
    avg_rtt         REAL,
    min_rtt         REAL,
    max_rtt         REAL,
    packet_loss     REAL,
    hop_count       INTEGER,
    result_count    INTEGER DEFAULT 0,
    raw_result      JSONB
);
SELECT create_hypertable('atlas_measurements', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS atlas_msm_idx    ON atlas_measurements (measurement_id, time DESC);
CREATE INDEX IF NOT EXISTS atlas_target_idx ON atlas_measurements (target, time DESC);

-- AS HEALTH SCORES - time series per AS (asn=0 = global)
CREATE TABLE IF NOT EXISTS as_health (
    time            TIMESTAMPTZ NOT NULL,
    asn             BIGINT NOT NULL,
    health_score    REAL,
    prefix_count_v4 INTEGER,
    prefix_count_v6 INTEGER,
    bgp_update_rate REAL,
    rpki_coverage   REAL,
    anomaly_count   INTEGER DEFAULT 0
);
SELECT create_hypertable('as_health', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS as_health_asn_idx ON as_health (asn, time DESC);
-- Provider evidence and source freshness (also available as a native migration).
BEGIN;
CREATE TABLE IF NOT EXISTS source_status (
 source TEXT PRIMARY KEY, last_attempt TIMESTAMPTZ NOT NULL,
 last_success TIMESTAMPTZ, last_observation TIMESTAMPTZ,
 state TEXT NOT NULL, records BIGINT NOT NULL DEFAULT 0,
 detail JSONB NOT NULL DEFAULT '{}', error_code TEXT
);
-- Latest evidence per provider identity. Historical raw BGP remains separate.
CREATE TABLE IF NOT EXISTS source_evidence (
 source TEXT NOT NULL, evidence_id TEXT NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL, asn BIGINT, family SMALLINT,
 resource TEXT, payload JSONB NOT NULL,
 PRIMARY KEY(source,evidence_id)
);
CREATE INDEX IF NOT EXISTS source_evidence_recent ON source_evidence(source,observed_at DESC);
CREATE INDEX IF NOT EXISTS source_evidence_asn ON source_evidence(asn,observed_at DESC) WHERE asn IS NOT NULL;
GRANT SELECT,INSERT,UPDATE ON source_status,source_evidence TO inetuser;
COMMIT;

-- Selected observer maintained tables; additive native migration also provided.
BEGIN;
CREATE TABLE IF NOT EXISTS routing_observers (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, collector TEXT NOT NULL, peer_ip INET NOT NULL,
 peer_asn BIGINT NOT NULL CHECK(peer_asn BETWEEN 1 AND 4294967295),
 mode TEXT NOT NULL CHECK(mode IN ('ris-live','mrt')), metadata JSONB NOT NULL,
 epoch BIGINT NOT NULL DEFAULT 1, generation UUID NOT NULL,
 stream_state TEXT NOT NULL DEFAULT 'starting', peer_state TEXT NOT NULL DEFAULT 'unknown',
 baseline_state TEXT NOT NULL DEFAULT 'required', baseline_at TIMESTAMPTZ,
 reset_at TIMESTAMPTZ, committed_at TIMESTAMPTZ, event_at TIMESTAMPTZ, received_at TIMESTAMPTZ,
 sequence BIGINT NOT NULL DEFAULT 0, messages BIGINT NOT NULL DEFAULT 0,
 updates BIGINT NOT NULL DEFAULT 0, reconnects BIGINT NOT NULL DEFAULT 0,
 snapshot_v4 BIGINT NOT NULL DEFAULT 0, snapshot_v6 BIGINT NOT NULL DEFAULT 0,
 error_code TEXT, UNIQUE(source,collector,peer_ip)
);
CREATE TABLE IF NOT EXISTS routing_messages (
 observer TEXT NOT NULL REFERENCES routing_observers(id), message_id TEXT NOT NULL,
 received_at TIMESTAMPTZ NOT NULL, event_at TIMESTAMPTZ NOT NULL, sequence BIGINT NOT NULL,
 payload JSONB NOT NULL, PRIMARY KEY(observer,message_id)
);
CREATE INDEX IF NOT EXISTS routing_messages_recent ON routing_messages(observer,received_at DESC);
CREATE TABLE IF NOT EXISTS routing_routes (
 observer TEXT NOT NULL REFERENCES routing_observers(id), prefix CIDR NOT NULL,
 epoch BIGINT NOT NULL, generation UUID NOT NULL, event_at TIMESTAMPTZ NOT NULL,
 rank SMALLINT NOT NULL, event_order TEXT NOT NULL, present BOOLEAN NOT NULL,
 origin_asn BIGINT CHECK(origin_asn BETWEEN 1 AND 4294967295), path JSONB NOT NULL,
 attrs JSONB NOT NULL, evidence_id TEXT NOT NULL,
 PRIMARY KEY(observer,prefix)
);
CREATE INDEX IF NOT EXISTS routing_routes_prefix ON routing_routes USING gist(prefix inet_ops);
CREATE INDEX IF NOT EXISTS routing_routes_origin ON routing_routes(observer,origin_asn) WHERE present;
CREATE TABLE IF NOT EXISTS routing_gaps (
 id UUID PRIMARY KEY, observer TEXT NOT NULL REFERENCES routing_observers(id),
 opened_at TIMESTAMPTZ NOT NULL DEFAULT now(), from_at TIMESTAMPTZ, until_at TIMESTAMPTZ,
 reason TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', repaired_at TIMESTAMPTZ,
 error_code TEXT
);
CREATE INDEX IF NOT EXISTS routing_gaps_pending ON routing_gaps(observer,opened_at) WHERE state<>'repaired';
CREATE UNIQUE INDEX IF NOT EXISTS routing_gaps_one_open ON routing_gaps(observer) WHERE until_at IS NULL;
CREATE TABLE IF NOT EXISTS routing_archives (
 observer TEXT NOT NULL REFERENCES routing_observers(id), url TEXT NOT NULL,
 generation UUID NOT NULL, kind TEXT NOT NULL, start_at TIMESTAMPTZ NOT NULL,
 until_at TIMESTAMPTZ NOT NULL, sha256 TEXT NOT NULL, bytes BIGINT NOT NULL,
 records BIGINT NOT NULL, audit JSONB NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(observer,url,generation)
);
GRANT SELECT,INSERT,UPDATE ON routing_observers,routing_messages,routing_routes,routing_gaps,routing_archives TO inetuser;
COMMIT;
