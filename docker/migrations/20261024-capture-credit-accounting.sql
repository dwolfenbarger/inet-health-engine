BEGIN;
CREATE SEQUENCE IF NOT EXISTS inet_retention.byte_credits MINVALUE 0 START 0;
CREATE SEQUENCE IF NOT EXISTS inet_retention.record_credits MINVALUE 0 START 0;
SELECT setval('inet_retention.byte_credits',GREATEST((SELECT used_bytes FROM inet_retention.policy WHERE id),(SELECT last_value FROM inet_retention.byte_credits)),true);
SELECT setval('inet_retention.record_credits',GREATEST((SELECT records FROM inet_retention.policy WHERE id),(SELECT last_value FROM inet_retention.record_credits)),true);
ALTER TABLE inet_retention.policy ADD COLUMN IF NOT EXISTS accounted_xid bigint NOT NULL DEFAULT 0;
ALTER TABLE inet_retention.gaps ADD COLUMN IF NOT EXISTS last_xid bigint NOT NULL DEFAULT 0;
ALTER TABLE inet_retention.gaps ADD COLUMN IF NOT EXISTS count_basis text NOT NULL DEFAULT 'minimum skipped candidates; at most one marker per source/reason/transaction';
CREATE OR REPLACE FUNCTION inet_retention.note_gap(s text,r text) RETURNS void LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,inet_retention AS $$
BEGIN
 INSERT INTO inet_retention.gaps(source,reason,first_at,last_at,skipped_candidates,last_xid)
  VALUES(s,r,clock_timestamp(),clock_timestamp(),1,txid_current())
 ON CONFLICT(source,reason) DO UPDATE SET last_at=excluded.last_at,
  skipped_candidates=inet_retention.gaps.skipped_candidates+1,last_xid=excluded.last_xid
 WHERE inet_retention.gaps.last_xid<>excluded.last_xid;
END $$;
CREATE OR REPLACE FUNCTION inet_retention.append_version(s text,k text,e text,t timestamptz,
 a bigint,f smallint,r text,p jsonb) RETURNS void LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,inet_retention AS $$
DECLARE policy_row inet_retention.policy%ROWTYPE; n bigint; h text; previous text; skip_reason text; reserved_bytes bigint; reserved_records bigint;
BEGIN
 SELECT * INTO policy_row FROM inet_retention.policy WHERE id;
 IF NOT s=ANY(policy_row.sources) OR policy_row.enabled_until IS NULL THEN RETURN; END IF;
 -- Never let an optional retained-history writer stall another live provider.
 IF NOT pg_try_advisory_xact_lock(hashtext('inet-retention-capture-budget')) THEN
  PERFORM inet_retention.note_gap(s,'concurrent_capture_busy');
  RETURN;
 END IF;
 SELECT * INTO policy_row FROM inet_retention.policy WHERE id;
 IF NOT s=ANY(policy_row.sources) OR policy_row.enabled_until IS NULL THEN RETURN; END IF;
 h:=encode(sha256(convert_to(jsonb_build_object('asn',a,'family',f,'resource',r,'payload',p)::text,'UTF8')),'hex');
 SELECT content_hash INTO previous FROM inet_retention.evidence
 WHERE source=s AND kind=k AND evidence_id=e AND observed_at=t ORDER BY id DESC LIMIT 1;
 IF previous=h THEN RETURN; END IF;
 n:=octet_length(convert_to(p::text,'UTF8'))+octet_length(e)+octet_length(coalesce(r,''))+256;
 SELECT last_value INTO reserved_bytes FROM inet_retention.byte_credits;
 SELECT last_value INTO reserved_records FROM inet_retention.record_credits;
 skip_reason:=CASE WHEN clock_timestamp()>=policy_row.enabled_until THEN 'capture_window_ended'
  WHEN n>65536 THEN 'record_size_limit'
  WHEN reserved_bytes+n>policy_row.max_bytes OR reserved_records>=policy_row.max_records THEN 'capture_budget' END;
 IF skip_reason IS NOT NULL THEN
  PERFORM inet_retention.note_gap(s,skip_reason);
  RETURN;
 END IF;
 INSERT INTO inet_retention.evidence(source,kind,evidence_id,observed_at,asn,family,resource,payload,content_hash,serialized_bytes)
 VALUES(s,k,e,t,a,f,r,p,h,n);
 PERFORM setval('inet_retention.byte_credits',reserved_bytes+n,true);
 PERFORM setval('inet_retention.record_credits',reserved_records+1,true);
END $$;
CREATE OR REPLACE FUNCTION inet_retention.account_commit() RETURNS trigger LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,inet_retention AS $$
BEGIN
 IF (SELECT accounted_xid FROM inet_retention.policy WHERE id)<>txid_current() THEN
  UPDATE inet_retention.policy SET used_bytes=(SELECT last_value FROM inet_retention.byte_credits),
   records=(SELECT last_value FROM inet_retention.record_credits),accounted_xid=txid_current() WHERE id;
 END IF;
 RETURN NULL;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='retained_account_commit' AND tgrelid='inet_retention.evidence'::regclass) THEN
  CREATE CONSTRAINT TRIGGER retained_account_commit AFTER INSERT ON inet_retention.evidence
   DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION inet_retention.account_commit();
 END IF;
END $$;
REVOKE ALL ON FUNCTION inet_retention.note_gap(text,text),inet_retention.account_commit() FROM PUBLIC;
COMMIT;
