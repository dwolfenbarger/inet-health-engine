"""Synthetic sealed-segment rollup qualification on the retained private Docker PG.

A sequence watermark is safe only inside an immutable, sealed input segment.
This is not a live identity-sequence consumer or a production journal adapter.
"""
import hashlib,json,statistics,subprocess,time
from pathlib import Path
PG='inet-pg-qa-20261027-v3';SCHEMA='inet_rollup_qa_20261027'
ROOT=Path.cwd()/'synthetic-history-20261027';REPORT=ROOT/'incremental-rollup.json'
def cmd(args,sql=None,timeout=45,check=True):
 r=subprocess.run(args,input=sql,text=True,capture_output=True,timeout=timeout,encoding='utf-8')
 if check and r.returncode:raise RuntimeError((args,r.returncode,r.stdout[-1000:],r.stderr[-2000:]))
 return r

def pg(sql):
 text=cmd(['docker','exec','-i',PG,'psql','-XqAt','-v','ON_ERROR_STOP=1','-U','postgres','-d','postgres'],sql).stdout.strip()
 return json.loads(text) if text else None

def main():
 inspect=json.loads(cmd(['docker','inspect',PG]).stdout)[0]
 assert inspect['Config']['Labels'].get('inet.synthetic-qualification')=='20261027'
 assert not inspect['State']['Running'] and inspect['HostConfig']['NetworkMode']=='none'
 assert inspect['HostConfig']['Memory']==2*1024**3 and inspect['HostConfig']['NanoCpus']==2_000_000_000
 source=ROOT/'synthetic.csv';input_sha=hashlib.sha256(source.read_bytes()).hexdigest()
 assert input_sha=='a114e1e1f4f63f493b54fdca05994cd9ea86f11c0f45c6fe0f4dc71a4e9ce82b'
 cmd(['docker','start',PG]);started=True
 try:
  for _ in range(30):
   try:pg('SELECT 1');break
   except RuntimeError:time.sleep(.5)
  assert pg("SELECT to_json(count(*)) FROM public.events")==1_000_000
  assert not pg("SELECT to_json(EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='"+SCHEMA+"'))"),'Refuse to replace retained qualification schema'
  prefix='SET search_path='+SCHEMA+',public; SET statement_timeout=\'30s\'; SET lock_timeout=\'500ms\'; SET jit=off; '
  pg('CREATE SCHEMA '+SCHEMA+';'+prefix+'''
   CREATE TABLE segments(id text PRIMARY KEY,sha text NOT NULL,declared_rows bigint NOT NULL,sealed boolean NOT NULL DEFAULT false);
   CREATE TABLE receipts(segment text NOT NULL REFERENCES segments(id),ordinal bigint NOT NULL,ts double precision NOT NULL,origin_asn bigint,change_type text,collector text,PRIMARY KEY(segment,ordinal));
   CREATE TABLE progress(segment text PRIMARY KEY REFERENCES segments(id),last_ordinal bigint NOT NULL DEFAULT 0);
   CREATE TABLE counts(bucket bigint NOT NULL,origin_asn bigint,change_type text,collector text,n bigint NOT NULL CHECK(n>0),UNIQUE NULLS NOT DISTINCT(bucket,origin_asn,change_type,collector));
   INSERT INTO segments VALUES('sealed-a','''+"'"+input_sha+"'"+''',1000000,false);
   INSERT INTO progress VALUES('sealed-a',0);
   INSERT INTO receipts SELECT 'sealed-a',row_number() OVER(ORDER BY ts,prefix,origin_asn,change_type,collector,peer_asn),ts,origin_asn,change_type,collector FROM public.events;
   CREATE FUNCTION consume(wanted text,expected_sha text,budget int) RETURNS jsonb LANGUAGE plpgsql AS $$
   DECLARE source segments; cp bigint; finish bigint; changed bigint;
   BEGIN
    IF budget<1 OR budget>10000 THEN RAISE EXCEPTION 'Batch budget'; END IF;
    SELECT * INTO STRICT source FROM segments WHERE id=wanted FOR SHARE;
    IF source.sha<>expected_sha THEN RETURN jsonb_build_object('status','manifest_mismatch','processed',0); END IF;
    IF NOT source.sealed THEN RETURN jsonb_build_object('status','unsealed','processed',0); END IF;
    SELECT last_ordinal INTO STRICT cp FROM progress WHERE segment=wanted FOR UPDATE;
    SELECT max(ordinal),count(*) INTO finish,changed FROM (SELECT ordinal FROM receipts WHERE segment=wanted AND ordinal>cp ORDER BY ordinal LIMIT budget) q;
    IF changed=0 THEN RETURN jsonb_build_object('status','caught_up','processed',0,'last_ordinal',cp); END IF;
    IF finish-cp<>changed OR finish>source.declared_rows THEN RAISE EXCEPTION 'Sealed receipt gap'; END IF;
    INSERT INTO counts(bucket,origin_asn,change_type,collector,n)
     SELECT floor(ts/3600)::bigint,origin_asn,change_type,collector,count(*) FROM receipts WHERE segment=wanted AND ordinal>cp AND ordinal<=finish GROUP BY 1,2,3,4
     ON CONFLICT(bucket,origin_asn,change_type,collector) DO UPDATE SET n=counts.n+excluded.n;
    UPDATE progress SET last_ordinal=finish WHERE segment=wanted;
    RETURN jsonb_build_object('status','processing','processed',changed,'last_ordinal',finish);
   END $$;''')
  call="SELECT consume('sealed-a','"+input_sha+"',10000)"
  assert pg(prefix+call)['status']=='unsealed'
  pg(prefix+"UPDATE segments SET sealed=true WHERE id='sealed-a';")
  # Failure after derived writes rolls back both counts and checkpoint.
  pg(prefix+'BEGIN; '+call+'; ROLLBACK;')
  assert pg(prefix+"SELECT json_build_array((SELECT last_ordinal FROM progress WHERE segment='sealed-a'),(SELECT count(*) FROM counts))")==[0,0]
  before=pg('SELECT to_json(pg_current_wal_insert_lsn()::text)');at=time.monotonic()
  calls=0
  while True:
   row=pg(prefix+call);calls+=1
   if row['processed']==0:break
  elapsed=time.monotonic()-at;after=pg('SELECT to_json(pg_current_wal_insert_lsn()::text)')
  wal=pg("SELECT to_json(pg_wal_lsn_diff('"+after+"','"+before+"'))")
  original=pg(prefix+'SELECT to_json(sum(n)) FROM counts');assert original==1_000_000
  assert pg(prefix+call)['processed']==0 and pg(prefix+'SELECT to_json(sum(n)) FROM counts')==original
  assert pg(prefix+"SELECT consume('sealed-a','wrong-hash',10000)")['status']=='manifest_mismatch'
  # Later-arriving old event time belongs to a NEW sealed receipt segment.
  pg(prefix+"INSERT INTO segments VALUES('late-b','synthetic-late',3,true); INSERT INTO progress VALUES('late-b',0); INSERT INTO receipts VALUES('late-b',1,1600000000,NULL,'withdraw',NULL),('late-b',2,1600000001,64512,'announce','fixture-late'),('late-b',3,1600000002,64512,'announce','fixture-late');")
  assert pg(prefix+"SELECT consume('late-b','synthetic-late',10000)")['processed']==3
  mismatch=pg(prefix+'''WITH expected AS (SELECT floor(ts/3600)::bigint bucket,origin_asn,change_type,collector,count(*) n FROM receipts GROUP BY 1,2,3,4)
   SELECT to_json(count(*)) FROM ((TABLE expected EXCEPT TABLE counts) UNION ALL (TABLE counts EXCEPT TABLE expected)) q''')
  assert mismatch==0 and pg(prefix+'SELECT to_json(sum(n)) FROM counts')==1_000_003
  # An ordinal gap refuses progress rather than silently claiming completeness.
  pg(prefix+"INSERT INTO segments VALUES('gap-c','synthetic-gap',3,true); INSERT INTO progress VALUES('gap-c',0); INSERT INTO receipts VALUES('gap-c',1,1600000000,64512,'announce','gap'),('gap-c',3,1600000001,64512,'announce','gap');")
  refused=cmd(['docker','exec','-i',PG,'psql','-XqAt','-v','ON_ERROR_STOP=1','-U','postgres'],prefix+"SELECT consume('gap-c','synthetic-gap',10000)",check=False)
  assert refused.returncode!=0 and 'Sealed receipt gap' in refused.stderr
  assert pg(prefix+"SELECT to_json(last_ordinal) FROM progress WHERE segment='gap-c'")==0
  pg(prefix+'ANALYZE receipts; ANALYZE counts;')
  raw="SELECT origin_asn,change_type,count(*) n FROM receipts WHERE segment<>'gap-c' GROUP BY 1,2 ORDER BY 1,2"
  roll='SELECT origin_asn,change_type,sum(n)::bigint n FROM counts GROUP BY 1,2 ORDER BY 1,2'
  queries={};results=[]
  for name,sql in [('raw',raw),('hourly_rollup',roll)]:
   times=[]
   for _ in range(5):times.append(pg(prefix+'EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) '+sql)[0]['Execution Time']/1000)
   results.append(pg(prefix+"SELECT json_agg(row_to_json(q)) FROM ("+sql+")q"));queries[name]={'warm_median_seconds':statistics.median(times[1:]),'seconds':times}
  assert results[0]==results[1]
  report={'pass':True,'scope':'synthetic 1M observations plus three late arrivals, hourly count summaries, retained Vengeance Docker PG17/2CPU/2GiB; not a production journal adapter or production IOPS claim','input_sha256':input_sha,'segment_rows':1_000_003,'consumer_calls':calls,'client_elapsed_seconds':elapsed,'cluster_wal_bytes':wal,'receipt_bytes':pg(prefix+"SELECT to_json(pg_total_relation_size('receipts'))"),'summary_bytes':pg(prefix+"SELECT to_json(pg_total_relation_size('counts'))"),'summary_rows':pg(prefix+'SELECT to_json(count(*)) FROM counts'),'queries':queries,'checks':['unsealed input refused','transaction rollback preserves counts and checkpoint','duplicate replay idempotent','manifest mismatch refused','late event time updates older bucket through new sealed segment','unknown origin/collector remain NULL','sealed ordinal gap refused without cursor progress','full exact summary reconciliation'],'limitations':['Requires immutable, verified segment identities/counts; identity sequences on an open live table are not a safe commit watermark.','Count summaries retain no event order, AS paths or flap/hijack proof. Raw diagnostic evidence and continuity gaps remain authoritative.','Synthetic cardinality and hourly resolution differ from the earlier real six-column minute-summary trial. No twelve-month capacity inference.']}
  REPORT.write_text(json.dumps(report,indent=2));print('PASS_INCREMENTAL_HISTORY',json.dumps(report),flush=True)
 finally:
  if started:cmd(['docker','stop','--time','15',PG],timeout=25);print('OWN_PG_STOPPED_EVIDENCE_RETAINED',flush=True)
if __name__=='__main__':main()
