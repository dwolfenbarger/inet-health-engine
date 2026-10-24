BEGIN;
CREATE OR REPLACE FUNCTION inet_retention.append_version(s text,k text,e text,t timestamptz,
 a bigint,f smallint,r text,p jsonb) RETURNS void LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,inet_retention AS $$
DECLARE policy_row inet_retention.policy%ROWTYPE; n bigint; h text; previous text; skip_reason text;
BEGIN
 SELECT * INTO policy_row FROM inet_retention.policy WHERE id;
 IF NOT s=ANY(policy_row.sources) OR policy_row.enabled_until IS NULL THEN RETURN; END IF;
 -- Never let an optional retained-history writer stall another live provider.
 IF NOT pg_try_advisory_xact_lock(hashtext('inet-retention-capture-budget')) THEN
  INSERT INTO inet_retention.gaps VALUES(s,'concurrent_capture_busy',clock_timestamp(),clock_timestamp(),1)
   ON CONFLICT(source,reason) DO UPDATE SET last_at=excluded.last_at,
    skipped_candidates=inet_retention.gaps.skipped_candidates+1;
  RETURN;
 END IF;
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
COMMIT;
