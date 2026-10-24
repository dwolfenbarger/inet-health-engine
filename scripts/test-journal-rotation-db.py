"""Disposable PostgreSQL integration checks; no writes to the application database."""
import asyncio,hashlib,json,subprocess,sys,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'collectors'))
from collectors.journal_rotation import prepare,retire,usage,trim_keys
UTC=timezone.utc
async def main(database):
 import asyncpg
 conn=await asyncpg.connect(database=database,user='postgres',host='/var/run/postgresql')
 n=0
 async def check(ok,label):
  nonlocal n
  assert ok,label;n+=1
 async def rejected(call,label):
  try:await call
  except (ValueError,RuntimeError):await check(True,label)
  else:raise AssertionError(label)
 try:
  await conn.execute((ROOT/'docker/migrations/20261023-routing-state.sql').read_text())
  await conn.execute((ROOT/'docker/migrations/20261024-journal-rotation.sql').read_text())
  now=datetime.now(UTC);old=now-timedelta(hours=30);generation=uuid.uuid4()
  await conn.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation,messages,sequence) VALUES('test','ris-continuous','rrc11','192.0.2.1',4288000245,'ris-live','{}',$1,2,2)",generation)
  await conn.executemany("INSERT INTO routing_messages VALUES('test',$1,$2,$2,$3,$4::jsonb)",
   [('a',old,1,json.dumps({'test':'high ASN4288000245'})),('b',old+timedelta(minutes=1),2,json.dumps({'as_set':[13335,4288000245]}))])
  await prepare(conn,now);await prepare(conn,now)
  await check(await conn.fetchval("SELECT relkind='p' FROM pg_class WHERE oid='routing_messages'::regclass"),'partitioned journal')
  await check(await conn.fetchval('SELECT count(*) FROM routing_messages_bootstrap')==2,'bootstrap payloads preserved')
  await check(await conn.fetchval('SELECT count(*) FROM routing_message_keys')==2,'identities copied')
  await check(await conn.fetchval("SELECT count(*) FROM routing_journal_segments WHERE name LIKE 'routing_messages_p%'")==50,'future partitions ready')
  await check(await conn.fetchval("SELECT sequence FROM routing_observers WHERE id='test'")==2,'checkpoints unchanged')
  await conn.execute("SET ROLE inetuser")
  dupe=await conn.fetchval("INSERT INTO routing_message_keys(observer,message_id,received_at,event_at,sequence) VALUES('test','a',now(),now(),3) ON CONFLICT DO NOTHING RETURNING sequence")
  await check(dupe is None,'duplicate still suppressed after rotation')
  await conn.execute("INSERT INTO routing_messages(observer,message_id,received_at,event_at,sequence,payload) VALUES('test','new',now(),now(),3,'{}')")
  await check(await conn.fetchval('SELECT count(*) FROM routing_messages')==1,'new journal accepts native writes')
  try:await conn.execute("INSERT INTO routing_messages(observer,message_id,received_at,event_at,sequence,payload,stored_at) VALUES('test','backdate',now(),now(),4,'{}',now()-interval '30 hours')");raise AssertionError('backdate permitted')
  except asyncpg.InsufficientPrivilegeError:await check(True,'app cannot backdate storage clock')
  await conn.execute('RESET ROLE')
  size=await usage(conn);await check(size['hot_bytes']>0 and size['key_bytes']>0,'real relation budget')
  expected=[{'observer':'test','records':2,'first':1,'last':2}]
  receipt={'verified':True,'local_retirement_eligible':True,'segment':'routing_messages_bootstrap','manifest_sha256':'a'*64,
    'storage_id':'e6a0939db1dd4a12a5dbec00ccf45296','environment':'development','observers':expected}
  await rejected(retire(conn,'routing_messages_bootstrap',receipt,now),'disabled rotation refuses retirement')
  await conn.execute('UPDATE routing_journal_policy SET enabled=true WHERE id')
  await rejected(retire(conn,'routing_messages_bootstrap',{**receipt,'verified':False},now),'unverified archive refused')
  await rejected(retire(conn,'routing_messages_bootstrap',{**receipt,'storage_id':'wrong'},now),'wrong storage refused')
  await rejected(retire(conn,'routing_messages_bootstrap',receipt,old+timedelta(hours=2)),'local24h protected')
  await rejected(retire(conn,'routing_messages_bootstrap',{**receipt,'observers':[]},now),'changed source refused')
  await check(await retire(conn,'routing_messages_bootstrap',receipt,now),'verified aged segment retired')
  await check(not await retire(conn,'routing_messages_bootstrap',receipt,now),'retirement idempotent')
  await check(await conn.fetchval('SELECT count(*) FROM routing_message_keys')==2,'keys survive payload expiry')
  await check(await conn.fetchval("SELECT sequence FROM routing_observers WHERE id='test'")==2,'retirement preserves checkpoints')
  await conn.execute("UPDATE routing_message_keys SET event_at=now()-interval '100 hours',stored_at=now()-interval '100 hours' WHERE message_id='a'")
  await check(await trim_keys(conn,now)==1,'bounded aged identity cleanup')
  await check(await conn.fetchval("SELECT dedup_floor_at IS NOT NULL FROM routing_observers WHERE id='test'"),'late-data floor retained')
  # Exercise the real writer and route transaction after a rotation.
  import types,importlib.util
  class Lease:
   async def __aenter__(self):return conn
   async def __aexit__(self,*args):return False
  class Pool:
   def acquire(self):return Lease()
  async def pool():return Pool()
  stub=types.ModuleType('collectors.db');stub.get_pg_pool=pool;sys.modules['collectors.db']=stub
  from collectors.routing_store import commit_messages
  observer={'id':'test','collector':'rrc11','peer_ip':'192.0.2.1','peer_asn':4288000245}
  frame={'type':'ris_message','data':{'type':'UPDATE','id':'live','host':'rrc11.ripe.net','peer':'192.0.2.1','peer_asn':4288000245,
    'timestamp':now.timestamp(),'path':[13335,4288000245],'announcements':[{'prefixes':['203.0.113.0/24'],'next_hop':'192.0.2.2'}],'withdrawals':[]}}
  await conn.execute("DELETE FROM routing_messages WHERE message_id='new'")
  await conn.execute('SET ROLE inetuser')
  await commit_messages(observer,[(frame,now)]);await commit_messages(observer,[(frame,now)])
  await check(await conn.fetchval("SELECT messages FROM routing_observers WHERE id='test'")==3,'live checkpoint advances once after duplicate replay')
  await check(await conn.fetchval("SELECT origin_asn FROM routing_routes WHERE observer='test' AND prefix='203.0.113.0/24'")==4288000245,'live route state survives journal rotation')
  late={'type':'ris_message','data':{**frame['data'],'type':'OPEN','id':'late','timestamp':(now-timedelta(hours=100)).timestamp()}}
  await commit_messages(observer,[(late,now)])
  await check(await conn.fetchval("SELECT epoch FROM routing_observers WHERE id='test'")==1,'old OPEN cannot reset current epoch')
  await check(await conn.fetchval("SELECT count(*) FROM routing_gaps WHERE reason='LateBeyondDedupHorizon'")==1,'expired horizon becomes an explicit gap')
  invalid={'type':'ris_message','data':{**frame['data'],'id':'invalid','host':'wrong.ripe.net'}}
  first={'type':'ris_message','data':{**frame['data'],'id':'rollback-first'}}
  try:await commit_messages(observer,[(first,now),(invalid,now)]);raise AssertionError('invalid observer accepted')
  except ValueError:pass
  await check(await conn.fetchval("SELECT count(*) FROM routing_message_keys WHERE message_id='rollback-first'")==0,'failed batch rolls back key and payload together')
  await conn.execute('RESET ROLE')
  await conn.execute("CREATE TABLE source_evidence(source text,evidence_id text,observed_at timestamptz,asn bigint,family smallint,resource text,payload jsonb,PRIMARY KEY(source,evidence_id)); CREATE TABLE source_status(source text PRIMARY KEY,last_attempt timestamptz,last_success timestamptz,last_observation timestamptz,state text,records bigint,detail jsonb,error_code text)")
  from collectors.source_evidence import save_cycle
  records=[{'id':str(i),'observed_at':now,'payload':{'value':i}} for i in range(1001)]
  failed=[*records];failed[501]={**failed[501],'payload':{'value':float('nan')}}
  try:await save_cycle('test-source',failed,{});raise AssertionError('NaN accepted')
  except ValueError:pass
  await check(await conn.fetchval('SELECT count(*) FROM source_evidence')==0,'later batch serialization failure rolls back earlier batches')
  await save_cycle('test-source',records,{})
  await check(await conn.fetchval("SELECT records FROM source_status WHERE source='test-source'")==1001,'batched provider cycle commits records and status atomically')
  print(json.dumps({'checks':n,'database':database,'result':'passed'}))
 finally:await conn.close()
if __name__=='__main__':
 db='inet_journal_test_'+uuid.uuid4().hex[:12];subprocess.run(['createdb',db],check=True)
 try:asyncio.run(main(db))
 finally:subprocess.run(['dropdb',db],check=True)
