-- Widen ASN storage without dropping or truncating observations.
-- All operations are transactional; compressed chunks require separate planning.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '10min';
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM timescaledb_information.chunks
             WHERE is_compressed AND hypertable_schema='public'
             AND hypertable_name IN ('bgp_updates','bgp_anomalies','as_health',
                 'network_events','rpki_status','traffic_metrics')) THEN
    RAISE EXCEPTION 'Compressed ASN tables require a decompression migration plan';
  END IF;
END $$;
ALTER TABLE public.bgp_updates
  ALTER COLUMN origin_asn TYPE BIGINT,
  ALTER COLUMN peer_asn TYPE BIGINT,
  ALTER COLUMN as_path TYPE BIGINT[] USING as_path::BIGINT[];
ALTER TABLE public.bgp_anomalies
  ALTER COLUMN origin_asn TYPE BIGINT,
  ALTER COLUMN expected_asn TYPE BIGINT;
ALTER TABLE public.network_events
  ALTER COLUMN affected_asns TYPE BIGINT[] USING affected_asns::BIGINT[];
ALTER TABLE public.traffic_metrics ALTER COLUMN asn TYPE BIGINT;
ALTER TABLE public.rpki_status ALTER COLUMN origin_asn TYPE BIGINT;
ALTER TABLE public.as_health ALTER COLUMN asn TYPE BIGINT;
ALTER TABLE public.prefix_origins ALTER COLUMN origin_asn TYPE BIGINT;
ALTER TABLE public.asn_peers
  ALTER COLUMN asn TYPE BIGINT,
  ALTER COLUMN peer_asn TYPE BIGINT;
COMMIT;
