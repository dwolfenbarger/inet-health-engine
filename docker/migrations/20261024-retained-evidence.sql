-- Additive, explicitly opt-in, bounded development qualification. No expiry.
BEGIN;
SET LOCAL lock_timeout='3s';
CREATE SCHEMA IF NOT EXISTS inet_retention;
CREATE TABLE IF NOT EXISTS inet_retention.policy (
 id boolean PRIMARY KEY DEFAULT true CHECK(id), enabled_until timestamptz,
 max_bytes bigint NOT NULL DEFAULT 536870912 CHECK(max_bytes>0),
 used_bytes bigint NOT NULL DEFAULT 0, max_records bigint NOT NULL DEFAULT 2000000,
 records bigint NOT NULL DEFAULT 0, started_at timestamptz,
 sources text[] NOT NULL DEFAULT ARRAY['atlas','atlas-mesh','ioda']
);
INSERT INTO inet_retention.policy(id) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS inet_retention.evidence (
 id bigserial PRIMARY KEY, source text NOT NULL, kind text NOT NULL,
 evidence_id text NOT NULL, observed_at timestamptz NOT NULL,
 captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 asn bigint, family smallint, resource text, payload jsonb NOT NULL,
 content_hash text NOT NULL, serialized_bytes bigint NOT NULL
);
CREATE INDEX IF NOT EXISTS retained_identity ON inet_retention.evidence
 (source,kind,evidence_id,observed_at,id DESC);
CREATE INDEX IF NOT EXISTS retained_time ON inet_retention.evidence(observed_at,source);
CREATE TABLE IF NOT EXISTS inet_retention.gaps (
 source text NOT NULL, reason text NOT NULL, first_at timestamptz NOT NULL,
 last_at timestamptz NOT NULL, skipped_candidates bigint NOT NULL,
 PRIMARY KEY(source,reason)
);
CREATE TABLE IF NOT EXISTS inet_retention.summaries (
 grain text NOT NULL CHECK(grain IN ('hour','day')), bucket timestamptz NOT NULL,
 source text NOT NULL, family smallint NOT NULL, cohort text NOT NULL,
 algorithm text NOT NULL, computed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 document jsonb NOT NULL, PRIMARY KEY(grain,bucket,source,family,cohort,algorithm)
);
CREATE OR REPLACE FUNCTION inet_retention.append_version(s text,k text,e text,t timestamptz,
 a bigint,f smallint,r text,p jsonb) RETURNS void LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,inet_retention AS $$
DECLARE policy_row inet_retention.policy%ROWTYPE; n bigint; h text; previous text; skip_reason text;
BEGIN
 SELECT * INTO policy_row FROM inet_retention.policy WHERE id FOR UPDATE;
 IF NOT s=ANY(policy_row.sources) OR policy_row.enabled_until IS NULL THEN RETURN; END IF;
 h:=encode(sha256(convert_to(jsonb_build_object('asn',a,'family',f,'resource',r,'payload',p)::text,'UTF8')),'hex');
 SELECT content_hash INTO previous FROM inet_retention.evidence
 WHERE source=s AND kind=k AND evidence_id=e AND observed_at=t ORDER BY id DESC LIMIT 1;
 IF previous=h THEN RETURN; END IF;
 n:=octet_length(convert_to(p::text,'UTF8'))+octet_length(e)+octet_length(coalesce(r,''))+256;
 skip_reason:=CASE WHEN clock_timestamp()>=policy_row.enabled_until THEN 'capture_window_ended'
  WHEN n>65536 THEN 'record_size_limit'
  WHEN policy_row.used_bytes+n>policy_row.max_bytes OR policy_row.records>=policy_row.max_records THEN 'capture_budget' END;
 IF skip_reason IS NOT NULL THEN
  INSERT INTO inet_retention.gaps VALUES(s,skip_reason,clock_timestamp(),clock_timestamp(),1)
   ON CONFLICT(source,reason) DO UPDATE SET last_at=excluded.last_at,
    skipped_candidates=inet_retention.gaps.skipped_candidates+1;
  RETURN;
 END IF;
 INSERT INTO inet_retention.evidence(source,kind,evidence_id,observed_at,asn,family,resource,payload,content_hash,serialized_bytes)
 VALUES(s,k,e,t,a,f,r,p,h,n);
 UPDATE inet_retention.policy SET used_bytes=used_bytes+n,records=records+1 WHERE id;
END $$;
CREATE OR REPLACE FUNCTION inet_retention.capture_evidence() RETURNS trigger LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,inet_retention AS $$
DECLARE item jsonb; p jsonb;
BEGIN
 IF NEW.source NOT IN ('atlas','atlas-mesh','ioda') THEN RETURN NEW; END IF;
 IF NOT EXISTS(SELECT 1 FROM inet_retention.policy WHERE id AND enabled_until IS NOT NULL AND NEW.source=ANY(sources)) THEN RETURN NEW; END IF;
 IF NEW.source='atlas-mesh' THEN
  -- One version per actual sample, independent of overlapping fetch windows.
  FOR item IN SELECT value FROM jsonb_array_elements(coalesce(NEW.payload->'raw_stats'->'data','[]')) LOOP
   p:=jsonb_build_object('sent',item->'sent','received',item->'received','rtt_med',CASE WHEN (item->>'received')::numeric>0 THEN item->'rtt_med' ELSE NULL END,
     'measurement_id',NEW.payload->'pair'->'measurement_id','source_probe',NEW.payload->'pair'->'source_probe',
     'target_probe',NEW.payload->'pair'->'target_probe','source_asn',NEW.payload->'source_asn',
     'target_asn',NEW.payload->'target_asn');
   PERFORM inet_retention.append_version(NEW.source,'sample',NEW.evidence_id,
    to_timestamp((item->>'timestamp')::double precision),NEW.asn,NEW.family,NEW.resource,p);
  END LOOP;
  p:=NEW.payload-ARRAY['raw_stats','fetched_at','raw_evidence_url','trace_evidence_url'];
 ELSE p:=NEW.payload;
 END IF;
 PERFORM inet_retention.append_version(NEW.source,'snapshot',NEW.evidence_id,NEW.observed_at,
  NEW.asn,NEW.family,NEW.resource,p);
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='retain_provider_evidence' AND tgrelid='public.source_evidence'::regclass) THEN
  CREATE TRIGGER retain_provider_evidence AFTER INSERT OR UPDATE ON public.source_evidence
   FOR EACH ROW EXECUTE FUNCTION inet_retention.capture_evidence();
 END IF;
END $$;
CREATE OR REPLACE FUNCTION inet_retention.capture_cycle() RETURNS trigger LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,inet_retention AS $$
BEGIN
 PERFORM inet_retention.append_version(NEW.source,'cycle',NEW.source,NEW.last_attempt,
  NULL::bigint,NULL::smallint,NULL::text,jsonb_build_object('state',NEW.state,'records',NEW.records,
  'last_success',NEW.last_success,'last_observation',NEW.last_observation,'error_code',NEW.error_code,'detail',NEW.detail));
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='retain_provider_cycle' AND tgrelid='public.source_status'::regclass) THEN
  CREATE TRIGGER retain_provider_cycle AFTER INSERT OR UPDATE ON public.source_status
   FOR EACH ROW EXECUTE FUNCTION inet_retention.capture_cycle();
 END IF;
END $$;
-- Application may read retained data; cannot rewrite or remove evidence or call privileged functions.
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA inet_retention FROM PUBLIC;
GRANT USAGE ON SCHEMA inet_retention TO inetuser;
GRANT SELECT ON ALL TABLES IN SCHEMA inet_retention TO inetuser;
COMMIT;
