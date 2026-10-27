"""Compare bounded, whole sealed-segment aggregation with row-batch rollups.

Synthetic private Docker qualification only. No production journal integration.
"""
import concurrent.futures,hashlib,importlib.util,json,threading,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('base',Path(__file__).with_name('qualify-history-incremental.py'));base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
PG=base.PG;SCHEMA=base.SCHEMA;ROOT=base.ROOT;cmd=base.cmd;pg=base.pg

def main():
 prior=json.loads((ROOT/'incremental-rollup.json').read_text());assert prior['pass']
 inspect=json.loads(cmd(['docker','inspect',PG]).stdout)[0]
 assert not inspect['State']['Running'] and inspect['Config']['Labels'].get('inet.synthetic-qualification')=='20261027'
 assert inspect['HostConfig']['NetworkMode']=='none' and inspect['HostConfig']['Memory']==2*1024**3 and inspect['HostConfig']['NanoCpus']==2_000_000_000
 sha=hashlib.sha256((ROOT/'synthetic.csv').read_bytes()).hexdigest();assert sha==prior['input_sha256']
 cmd(['docker','start',PG])
 try:
  for _ in range(30):
   try:pg('SELECT 1');break
   except RuntimeError:time.sleep(.5)
  prefix='SET search_path='+SCHEMA+",public; SET statement_timeout='30s'; SET lock_timeout='3s'; SET jit=off; "
  pg(prefix+'''
   CREATE TABLE counts_segment(LIKE counts INCLUDING ALL);
   CREATE TABLE processed_segments(id text PRIMARY KEY,sha text NOT NULL,rows bigint NOT NULL);
   CREATE FUNCTION guard_sealed_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
   DECLARE closed boolean;
   BEGIN
    IF TG_OP<>'INSERT' THEN
     SELECT sealed INTO closed FROM segments WHERE id=OLD.segment FOR SHARE;
     IF closed THEN RAISE EXCEPTION 'Immutable sealed segment'; END IF;
    END IF;
    IF TG_OP<>'DELETE' THEN
     SELECT sealed INTO closed FROM segments WHERE id=NEW.segment FOR SHARE;
     IF closed THEN RAISE EXCEPTION 'Immutable sealed segment'; END IF;
     RETURN NEW;
    END IF;
    RETURN OLD;
   END $$;
   CREATE TRIGGER guard_sealed_receipt BEFORE INSERT OR UPDATE OR DELETE ON receipts FOR EACH ROW EXECUTE FUNCTION guard_sealed_receipt();
   CREATE FUNCTION guard_sealed_manifest() RETURNS trigger LANGUAGE plpgsql AS $$
   BEGIN
    IF OLD.sealed AND ((NEW.id,NEW.sha,NEW.declared_rows,NEW.sealed) IS DISTINCT FROM (OLD.id,OLD.sha,OLD.declared_rows,OLD.sealed)) THEN RAISE EXCEPTION 'Immutable sealed manifest'; END IF;
    RETURN NEW;
   END $$;
   CREATE TRIGGER guard_sealed_manifest BEFORE UPDATE ON segments FOR EACH ROW EXECUTE FUNCTION guard_sealed_manifest();
   CREATE FUNCTION consume_segment(wanted text,expected_sha text) RETURNS jsonb LANGUAGE plpgsql AS $$
   DECLARE source segments; prior processed_segments; n bigint; first_ordinal bigint; last_ordinal bigint;
   BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('inet-segment-rollup:'||wanted,0));
    SELECT * INTO STRICT source FROM segments WHERE id=wanted FOR SHARE;
    IF source.sha<>expected_sha THEN RETURN jsonb_build_object('status','manifest_mismatch','processed',0); END IF;
    IF NOT source.sealed THEN RETURN jsonb_build_object('status','unsealed','processed',0); END IF;
    IF source.declared_rows<1 OR source.declared_rows>1000000 THEN RAISE EXCEPTION 'Segment admission budget'; END IF;
    SELECT * INTO prior FROM processed_segments WHERE id=wanted;
    IF FOUND THEN
     IF (prior.sha,prior.rows) IS DISTINCT FROM (source.sha,source.declared_rows) THEN RAISE EXCEPTION 'Processed manifest changed'; END IF;
     RETURN jsonb_build_object('status','already_processed','processed',0);
    END IF;
    SELECT count(*),min(ordinal),max(ordinal) INTO n,first_ordinal,last_ordinal FROM receipts WHERE segment=wanted;
    IF n<>source.declared_rows OR first_ordinal<>1 OR last_ordinal<>source.declared_rows THEN RAISE EXCEPTION 'Sealed receipt gap'; END IF;
    INSERT INTO counts_segment(bucket,origin_asn,change_type,collector,n)
      SELECT floor(ts/3600)::bigint,origin_asn,change_type,collector,count(*) FROM receipts WHERE segment=wanted GROUP BY 1,2,3,4 ORDER BY 1,2,3,4
      ON CONFLICT(bucket,origin_asn,change_type,collector) DO UPDATE SET n=counts_segment.n+excluded.n;
    INSERT INTO processed_segments VALUES(wanted,source.sha,n);
    RETURN jsonb_build_object('status','processed','processed',n);
   END $$;''')
  call="SELECT consume_segment('sealed-a','"+sha+"')"
  pg(prefix+'BEGIN; '+call+'; ROLLBACK;')
  assert pg(prefix+'SELECT json_build_array((SELECT count(*) FROM counts_segment),(SELECT count(*) FROM processed_segments))')==[0,0]
  # Never-published, empty test tables only; remove aborted-allocation bias.
  pg(prefix+'TRUNCATE counts_segment,processed_segments;')
  before=pg('SELECT to_json(pg_current_wal_insert_lsn()::text)');barrier=threading.Barrier(2)
  def consume():barrier.wait();return pg(prefix+call)
  at=time.monotonic()
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:consume(),range(2)))
  elapsed=time.monotonic()-at;after=pg('SELECT to_json(pg_current_wal_insert_lsn()::text)')
  wal=pg("SELECT to_json(pg_wal_lsn_diff('"+after+"','"+before+"'))")
  assert sorted(r['processed'] for r in results)==[0,1000000],results
  assert pg(prefix+call)['status']=='already_processed'
  assert pg(prefix+"SELECT consume_segment('late-b','synthetic-late')")['processed']==3
  assert pg(prefix+"SELECT consume_segment('late-b','bad-sha')")['status']=='manifest_mismatch'
  def refused(sql,reason):
   r=cmd(['docker','exec','-i',PG,'psql','-XqAt','-v','ON_ERROR_STOP=1','-U','postgres'],prefix+sql,check=False)
   assert r.returncode!=0 and reason in r.stderr,(r.returncode,r.stderr)
  refused("SELECT consume_segment('gap-c','synthetic-gap')",'Sealed receipt gap')
  refused("INSERT INTO receipts VALUES('sealed-a',1000001,1700000000,64512,'announce','fixture')",'Immutable sealed segment')
  refused("UPDATE segments SET sealed=false WHERE id='sealed-a'",'Immutable sealed manifest')
  mismatch=pg(prefix+'SELECT to_json(count(*)) FROM ((TABLE counts EXCEPT TABLE counts_segment) UNION ALL (TABLE counts_segment EXCEPT TABLE counts)) q')
  assert mismatch==0 and pg(prefix+'SELECT to_json(sum(n)) FROM counts_segment')==1_000_003
  report={'pass':True,'scope':'synthetic verified sealed-segment hourly counts on retained Vengeance Docker PG17/2CPU/2GiB; no production adapter or IOPS claim','input_sha256':sha,'source_rows':1000003,'concurrent_worker_results':results,'client_elapsed_seconds':elapsed,'cluster_wal_bytes':wal,'summary_bytes':pg(prefix+"SELECT to_json(pg_total_relation_size('counts_segment'))"),'summary_rows':pg(prefix+'SELECT to_json(count(*)) FROM counts_segment'),'prior_10000_row_batches':{k:prior[k] for k in ['consumer_calls','client_elapsed_seconds','cluster_wal_bytes','summary_bytes']},'checks':['complete exact counts versus prior independently batched result','two independent workers produce one atomic contribution','duplicate replay idempotent','transaction rollback preserves contribution ledger and counts','manifest mismatch refused','incomplete sealed segment refused','writes to sealed receipts refused','sealed manifest mutation refused','late event time updates older bucket through separately sealed segment','NULL origin/collector preserved'],'limitations':['1M-row maximum is only a synthetic projected-segment bound; real payload cardinality/temp/WAL/catchup/retention budgets still require qualification.','Sealing adapter must verify immutable file SHA/decoded count/source continuity and provider identities; no open-table identity watermark.','Count summaries never replace raw event order, AS paths, source gaps or flap/hijack proof.','WAL is an isolated Docker cluster LSN delta, not production physical IOPS.']}
  (ROOT/'sealed-segment-rollup.json').write_text(json.dumps(report,indent=2));print('PASS_SEALED_SEGMENT',json.dumps(report),flush=True)
 finally:
  cmd(['docker','stop','--time','15',PG],timeout=25);print('OWN_PG_STOPPED_EVIDENCE_RETAINED',flush=True)
if __name__=='__main__':main()
