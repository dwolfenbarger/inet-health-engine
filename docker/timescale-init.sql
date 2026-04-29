-- TimescaleDB initialization for inet-health-engine
-- All 7 hypertables. network_events and as_health were missing on initial VENGEANCE deploy
-- due to a syntax error in the original (missing comma after PRIMARY KEY clause).
-- Fixed 2026-04-27.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- BGP UPDATES - raw update stream
CREATE TABLE IF NOT EXISTS bgp_updates (
    time            TIMESTAMPTZ NOT NULL,
    prefix          TEXT NOT NULL,
    origin_asn      INTEGER,
    as_path         INTEGER[],
    communities     TEXT[],
    change_type     TEXT,
    collector       TEXT,
    peer_asn        INTEGER,
    next_hop        INET,
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
    origin_asn      INTEGER,
    expected_asn    INTEGER,
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
    asn             INTEGER,
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
    affected_asns           INTEGER[],
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
    origin_asn      INTEGER,
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
    asn             INTEGER NOT NULL,
    health_score    REAL,
    prefix_count_v4 INTEGER,
    prefix_count_v6 INTEGER,
    bgp_update_rate REAL,
    rpki_coverage   REAL,
    anomaly_count   INTEGER DEFAULT 0
);
SELECT create_hypertable('as_health', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS as_health_asn_idx ON as_health (asn, time DESC);