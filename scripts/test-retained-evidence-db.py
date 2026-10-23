"""Integration qualification in a disposable database. Run as postgres on UAT."""
import asyncio,importlib.util,json,subprocess,sys,time,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
async def test(db):
 import asyncpg
 conn=await asyncpg.connect(database=db,user='postgres',host='/var/run/postgresql')
 checks=0
 async def check(condition,message):
  nonlocal checks
  assert condition,message
  checks+=1
 try:
  await conn.execute('''CREATE TABLE source_evidence(source text,evidence_id text,observed_at timestamptz,asn bigint,family smallint,resource text,payload jsonb,PRIMARY KEY(source,evidence_id));
  CREATE TABLE source_status(source text PRIMARY KEY,last_attempt timestamptz,last_success timestamptz,last_observation timestamptz,state text,records bigint,detail jsonb,error_code text);''')
  migration=(ROOT/'docker/migrations/20261024-retained-evidence.sql').read_text()
  await conn.execute(migration);await conn.execute(migration)
  at=datetime(2026,10,23,10,tzinfo=timezone.utc)
  async def put(source,ident,payload):
   await conn.execute('''INSERT INTO source_evidence VALUES($1,$2,$3,4288000245,6,'test',$4::jsonb)
    ON CONFLICT(source,evidence_id) DO UPDATE SET payload=excluded.payload''',source,ident,at,json.dumps(payload))
  await put('atlas','one',{'rtt_ms':1})
  await check(await conn.fetchval('SELECT count(*) FROM inet_retention.evidence')==0,'default disabled')
  await conn.execute("UPDATE inet_retention.policy SET enabled_until=now()+interval '72 hours',started_at=now()")
  p={'kind':'ping','measurement_id':2001,'rtt_ms':10,'raw':{'sent':3,'rcvd':2}}
  await put('atlas','one',p);await put('atlas','one',p)
  await check(await conn.fetchval('SELECT count(*) FROM inet_retention.evidence')==1,'duplicate suppressed')
  p['rtt_ms']=20;await put('atlas','one',p);p['rtt_ms']=10;await put('atlas','one',p)
  await check(await conn.fetchval('SELECT count(*) FROM inet_retention.evidence')==3,'A B A corrections retained')
  await check(await conn.fetchval('SELECT max(asn) FROM inet_retention.evidence')==4288000245,'unsigned high ASN preserved')
  tx=conn.transaction();await tx.start();await put('atlas','rollback',p);await tx.rollback()
  await check(await conn.fetchval("SELECT count(*) FROM inet_retention.evidence WHERE evidence_id='rollback'")==0,'atomic rollback')
  mesh={'pair':{'measurement_id':123,'source_probe':1,'target_probe':2},'source_asn':42,'target_asn':None,
   'raw_stats':{'data':[{'timestamp':at.timestamp(),'sent':3,'received':0,'rtt_med':None},
    {'timestamp':at.timestamp()+60,'sent':3,'received':3,'rtt_med':8}]},'fetched_at':'first'}
  await put('atlas-mesh','pair',mesh);mesh['fetched_at']='second';await put('atlas-mesh','pair',mesh)
  await check(await conn.fetchval("SELECT count(*) FROM inet_retention.evidence WHERE source='atlas-mesh'")==3,'overlap only2 samples and1 slim snapshot')
  await check(not await conn.fetchval("SELECT bool_or(payload ? 'raw_stats') FROM inet_retention.evidence"),'raw arrays not duplicated')
  await put('rpki','excluded',p)
  await check(await conn.fetchval("SELECT count(*) FROM inet_retention.evidence WHERE source='rpki'")==0,'RPKI bulk excluded')
  await conn.execute("INSERT INTO source_status VALUES('atlas',now(),now(),now(),'partial',1,'{}',NULL)")
  await check(await conn.fetchval("SELECT count(*) FROM inet_retention.evidence WHERE kind='cycle'")==1,'cycle preserved')
  await put('atlas','malformed-counters',{'kind':'ping','measurement_id':3001,'rtt_ms':None,'raw':{'sent':'unknown','rcvd':True}})
  spec=importlib.util.spec_from_file_location('summaries',ROOT/'scripts/retained-summaries.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  result=await mod.summarize(conn,at.replace(hour=0),at.replace(hour=0)+timedelta(days=1))
  await check(result['summaries']==6,'hourly and daily cohorts')
  summary=json.loads(await conn.fetchval("SELECT document FROM inet_retention.summaries WHERE source='atlas-mesh' AND grain='hour'"))
  await check(summary['samples']==2 and summary['sent']==6 and summary['received']==3 and summary['loss_percent']==50 and summary['median_sample_medians_ms']==8,'packetweighted loss and null RTT')
  await mod.summarize(conn,at.replace(hour=0),at.replace(hour=0)+timedelta(days=1))
  await check(await conn.fetchval('SELECT count(*) FROM inet_retention.summaries')==6,'summary idempotence')
  invalid=json.loads(await conn.fetchval("SELECT document FROM inet_retention.summaries WHERE source='atlas' AND grain='hour' AND cohort='3001:ping'"))
  await check(invalid['sent'] is None and invalid['loss_percent'] is None,'invalid packet counters remain unknown')
  await check(summary['complete'] is False and summary['expected_samples'] is None,'missing remains unknown')
  await conn.execute("UPDATE inet_retention.policy SET max_bytes=used_bytes")
  await put('atlas','over-budget',p)
  await check(await conn.fetchval("SELECT skipped_candidates FROM inet_retention.gaps WHERE reason='capture_budget'")==1,'budget gap recorded')
  await check(await conn.fetchval("SELECT count(*) FROM source_evidence WHERE evidence_id='over-budget'")==1,'budget leaves current ingestion running')
  await conn.execute("UPDATE inet_retention.policy SET enabled_until=now()-interval '1 second',max_bytes=536870912")
  await put('atlas','expired-window',p)
  await check(await conn.fetchval("SELECT skipped_candidates FROM inet_retention.gaps WHERE reason='capture_window_ended'")==1,'finite window gap')
  await conn.execute("UPDATE inet_retention.policy SET enabled_until=now()+interval '72 hours',max_records=records")
  await put('atlas','record-budget',p)
  await check(await conn.fetchval("SELECT skipped_candidates FROM inet_retention.gaps WHERE reason='capture_budget'")==2,'record budget gap')
  await conn.execute("UPDATE inet_retention.policy SET max_records=2000000")
  await put('atlas','oversized',{'data':'x'*70000})
  await check(await conn.fetchval("SELECT skipped_candidates FROM inet_retention.gaps WHERE reason='record_size_limit'")==1,'oversized record gap')
  await conn.execute('GRANT INSERT,UPDATE,SELECT ON source_evidence TO inetuser')
  await conn.execute("SET ROLE inetuser")
  await put('atlas','application-write',p)
  await check(await conn.fetchval("SELECT count(*) FROM inet_retention.evidence WHERE evidence_id='application-write'")==1,'application write fires privileged trigger without direct function access')
  try:
   await conn.execute('DELETE FROM inet_retention.evidence');raise AssertionError('app can delete history')
  except asyncpg.InsufficientPrivilegeError:checks+=1
  try:
   await conn.execute('UPDATE inet_retention.evidence SET resource=NULL');raise AssertionError('app can rewrite history')
  except asyncpg.InsufficientPrivilegeError:checks+=1
  try:
   await conn.execute("SELECT inet_retention.append_version('atlas','snapshot','illegal',now(),NULL::bigint,NULL::smallint,NULL::text,'{}'::jsonb)");raise AssertionError('app can call privileged capture function')
  except asyncpg.InsufficientPrivilegeError:checks+=1
  await conn.execute('RESET ROLE')
  await conn.execute("UPDATE inet_retention.policy SET enabled_until=now()+interval '72 hours'")
  begin=time.monotonic()
  async with conn.transaction():
   for i in range(1000):await put('atlas',f'benchmark-{i}',p)
  print(json.dumps({'checks':checks,'isolated_database':db,'thousand_inserts_seconds':round(time.monotonic()-begin,3)}))
 finally:await conn.close()
if __name__=='__main__':
 db='inet_retention_test_'+uuid.uuid4().hex[:12]
 subprocess.run(['createdb',db],check=True)
 try:asyncio.run(test(db))
 finally:subprocess.run(['dropdb',db],check=True)
