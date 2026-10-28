"""Real PostgreSQL replay and ASGI contract qualification, isolated DEV only."""
import asyncio,json,os,socket,sys,uuid
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import AsyncMock,patch
assert socket.gethostname()=='inet-health-dev1'
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
sys.path[:0]=[str(ROOT/'collectors'),str(ROOT/'api')]
import asyncpg,httpx
from fastapi import FastAPI
from collectors.routing_diagnostic_worker import process_frames,cycle,maintenance,DiagnosticInputError
import collectors.routing_diagnostic_worker as worker
import api.routes_diagnostics as api
DB=os.environ.get('INET_DIAGNOSTICS_QA_DATABASE','inet_diag_qa_20261027');assert DB=='inet_diag_qa_20261027' or DB.startswith('inet_ret14_qa_');UTC=timezone.utc
async def main():
 from urllib.parse import urlsplit
 assert urlsplit(os.environ['TIMESCALE_DSN']).path=='/'+DB
 pool=await asyncpg.create_pool(dsn=os.environ['TIMESCALE_DSN'],min_size=1,max_size=4)
 checks=[];pending=None;tx=None;base=datetime.now(UTC)-timedelta(hours=2)
 o={'id':'qa-replay-a','collector':'rrc00','peer_ip':'192.0.2.1','peer_asn':64500}
 other={'id':'qa-replay-b','collector':'rrc00','peer_ip':'192.0.2.2','peer_asn':64510}
 def frame(seq,t,present=True,origin=64501,prefixes=None,kind='UPDATE',observer=o,path=None):
  at=base+timedelta(seconds=t);prefixes=prefixes or ['192.0.2.0/24']
  raw={'data':{'host':'rrc00.ripe.net','peer':observer['peer_ip'],'peer_asn':observer['peer_asn'],'type':kind,'timestamp':at.timestamp(),'id':str(seq),'path':path or [observer['peer_asn'],origin], 'announcements':[{'prefixes':prefixes}] if present else [],'withdrawals':[] if present else prefixes}}
  return {'message_id':str(seq),'sequence':seq,'event_at':at,'received_at':at,'payload':raw}
 async def checkpoint(conn,obs=o):return await conn.fetchrow('SELECT * FROM routing_diagnostic_cursors WHERE observer=$1',obs['id'])
 async def apply(frames,gaps=(),observer=o):
  async with pool.acquire() as c,c.transaction():return await process_frames(c,observer,await checkpoint(c,observer),frames,gaps)
 try:
  async with pool.acquire() as c:
   assert await c.fetchval('SELECT current_database()')==DB
   await c.execute('CREATE TABLE IF NOT EXISTS source_evidence(source text,evidence_id text,observed_at timestamptz,payload jsonb)')
   for obs in [o,other]:
    await c.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation) VALUES($1,'fixture',$2,$3::text::inet,$4,'ris-live','{}',$5)",obs['id'],obs['collector'],obs['peer_ip'],obs['peer_asn'],uuid.uuid4())
    await c.execute('INSERT INTO routing_diagnostic_cursors(observer,sequence,received_at,event_at,gap_seen,started_at) VALUES($1,0,$2,$2,$2,$2)',obs['id'],base)
  async with pool.acquire() as held,held.transaction():
   await held.fetchrow('SELECT id FROM routing_observers WHERE id=$1 FOR UPDATE',o['id'])
   async with pool.acquire() as c,c.transaction():
    await c.execute("SET LOCAL lock_timeout='200ms'")
    await c.execute("SET LOCAL statement_timeout='3s'")
    await process_frames(c,o,await checkpoint(c),[frame(1,1)],[])
  checks.append('Diagnostic evidence commits while collector observer row is FOR UPDATE locked')
  await apply([frame(i,i,i%2==1) for i in range(2,28)])
  assert await pool.fetchval("SELECT count(*) FROM routing_diagnostic_events WHERE observer=$1 AND candidate='bgp_flap'",o['id'])==1
  count=await pool.fetchval('SELECT count(*) FROM routing_diagnostic_events')
  await apply([frame(27,27)]);assert await pool.fetchval('SELECT count(*) FROM routing_diagnostic_events')==count
  checks.append('Presence flapping uses true transitions; replay does not duplicate evidence')
  class FailCheckpoint:
   def __init__(self,c):self.c=c
   def __getattr__(self,k):return getattr(self.c,k)
   async def execute(self,sql,*args):
    if sql.startswith('UPDATE routing_diagnostic_cursors'):raise RuntimeError('InjectedBeforeCheckpoint')
    return await self.c.execute(sql,*args)
  try:
   async with pool.acquire() as c,c.transaction():await process_frames(FailCheckpoint(c),o,await checkpoint(c),[frame(28,400,origin=64502)],[])
  except RuntimeError as e:assert str(e)=='InjectedBeforeCheckpoint'
  else:raise AssertionError('Failure injection did not run')
  assert await pool.fetchval('SELECT sequence FROM routing_diagnostic_cursors WHERE observer=$1',o['id'])==27
  assert await pool.fetchval('SELECT count(*) FROM routing_diagnostic_events')==count
  await apply([frame(28,400,origin=64502)])
  proof=json.loads(await pool.fetchval("SELECT evidence FROM routing_diagnostic_events WHERE observer=$1 AND candidate='bgp_hijack'",o['id']))
  assert proof['before']['path']==[64500,64501] and proof['facts']['expected_asn']==64501
  checks.append('Crash before checkpoint rolls back evidence/state/cursor; retry preserves prior origin/path')
  await apply([frame(29,800,origin=64503)],[{'opened_at':base+timedelta(seconds=700),'reason':'ReadError'}])
  assert await pool.fetchval('SELECT epoch FROM routing_diagnostic_cursors WHERE observer=$1',o['id'])==2
  reset=json.loads(await pool.fetchval("SELECT evidence FROM routing_diagnostic_events WHERE observer=$1 AND sequence=29 AND kind='baseline'",o['id']))
  assert reset['facts']['source_continuity']['reasons']==['SourceGap:ReadError']
  assert reset['facts']['baseline_reason']=='continuity_reset'
  assert await pool.fetchval("SELECT count(*) FROM routing_diagnostic_events WHERE observer=$1 AND candidate='bgp_hijack'",o['id'])==1
  await apply([frame(31,900,origin=64504)])
  await apply([frame(32,1000,kind='OPEN'),frame(33,1001,origin=64505,prefixes=['192.0.2.0/24','2001:db8:200::/48'],path=[64500,64531,64505])])
  assert await pool.fetchval('SELECT epoch FROM routing_diagnostic_cursors WHERE observer=$1',o['id'])==4
  prefixes=[f'198.18.{i}.0/24' for i in range(199)]+['2001:db8:200::/48']
  await apply([frame(34,1002,origin=64505,prefixes=prefixes[:-1],path=[64500,64530,64505]),frame(35,1003,False,prefixes=prefixes)])
  proof=json.loads(await pool.fetchval("SELECT evidence FROM routing_diagnostic_events WHERE observer=$1 AND candidate='withdrawal_surge'",o['id']))
  assert len(set(proof['facts']['distinct_prefixes']))==200
  assert proof['facts']['recorded_paths_complete'] and len(proof['facts']['withdrawn_prefix_paths'])==200
  involved=await pool.fetchval("SELECT involved_asns FROM routing_diagnostic_events WHERE observer=$1 AND candidate='withdrawal_surge'",o['id'])
  assert 64530 in involved and 64531 in involved
  groups=await api.candidate_groups(pool,1440,64531,None,'all','all',datetime.now(UTC))
  assert any(g['kind']=='withdrawal_surge' for g in groups)
  await apply([frame(i,1100+i-36,prefixes=['2001:db8:100::/48'],path=[64500,64520+i%2,64505]) for i in range(36,67)])
  assert await pool.fetchval("SELECT count(*) FROM routing_diagnostic_events WHERE observer=$1 AND candidate='bgp_flap'",o['id'])==2
  checks.append('Source/sequence/session gaps reset baselines; mixed-family distinct withdrawal surge and path-only flapping detected')
  with patch.object(worker,'MAX_BATCH_CHANGES',2):
   cp=await apply([frame(67,1200,prefixes=['203.0.113.0/24']),frame(68,1201,prefixes=['203.0.114.0/24','203.0.115.0/24'])]);assert cp['sequence']==67
   cp=await apply([frame(68,1201,prefixes=['203.0.114.0/24','203.0.115.0/24'])]);assert cp['sequence']==68
  for bad,code in [(frame(69,8000),'FutureEventTime'),(dict(frame(69,1300),message_id='wrong'),'JournalIdentityMismatch')]:
   try:await apply([bad])
   except DiagnosticInputError as e:assert str(e)==code
   else:raise AssertionError(code+' was not refused')
   assert await pool.fetchval('SELECT sequence FROM routing_diagnostic_cursors WHERE observer=$1',o['id'])==68
  checks.append('Batch boundaries resume without loss; corrupt/future input cannot advance the checkpoint')
  await apply([frame(1,1,observer=other)],observer=other)
  await pool.close();pool=await asyncpg.create_pool(dsn=os.environ['TIMESCALE_DSN'],min_size=1,max_size=4)
  assert await pool.fetchval('SELECT sequence FROM routing_diagnostic_cursors WHERE observer=$1',o['id'])==68
  checks.append('Actual connection restart retains committed progress and independent observer baselines')
  # Delayed committed transaction has a journal sequence beyond the first page's
  # committed head. It must never leak into that pinned investigation later.
  pending=await pool.acquire();tx=pending.transaction();await tx.start()
  await process_frames(pending,o,await checkpoint(pending),[frame(69,1300,origin=64506)],[])
  await apply([frame(2,1400,origin=64507,observer=other)],observer=other)
  class Cache:
   def __init__(self):self.values={};self.ttls=[]
   async def get(self,k):return self.values.get(k)
   async def setex(self,k,ttl,v):self.values[k]=v;self.ttls.append(ttl)
  cache=Cache();app=FastAPI();app.include_router(api.router)
  with patch.object(api,'get_pg_pool',AsyncMock(return_value=pool)),patch.object(api,'get_redis',AsyncMock(return_value=cache)):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://qa') as client:
    async def get(path,params=None,code=200):
     r=await client.get('/diagnostics'+path,params=params);assert r.status_code==code,(path,r.status_code,r.text[:300]);return r.json()
    params={'asn':64500,'minutes':1440,'limit':2};page=await get('/history',params);cursor=page['next_cursor'];assert cursor
    await tx.commit();await pool.release(pending);pending=None
    seen=set();n=0
    while True:
     for e in page['items']:
      assert e['event_id'] not in seen;seen.add(e['event_id']);assert not (e['observer']==o['id'] and e['sequence']==69)
     n+=len(page['items'])
     if not page['next_cursor']:break
     page=await get('/history',params|{'cursor':page['next_cursor']})
    assert n>200 and all(0<t<=300 for t in cache.ttls)
    await get('/history',params|{'cursor':cursor,'asn':64501},422)
    await get('/history',params|{'cursor':str(uuid.uuid4())},409)
    for resource,family in [('198.18.0.0/24','4'),('2001:db8:200::/48','6')]:
     rows=await get('/history',{'resource':resource,'family':family,'minutes':1440})
     assert any(x['candidate']=='withdrawal_surge' for x in rows['items'])
    mismatch=await get('/history',{'resource':'198.18.0.0/24','family':'6','minutes':1440});assert mismatch['items']==[]
    mismatch=await get('/history',{'asn':64531,'resource':'198.18.0.0/24','family':'4','minutes':1440});assert mismatch['items']==[]
    transit=await get('/history',{'asn':64531,'resource':'2001:db8:200::/48','family':'6','minutes':1440});assert any(r['candidate']=='withdrawal_surge' for r in transit['items'])
    row=next(x for x in (await get('/history',{'asn':64501,'minutes':1440}))['items'] if x['candidate']=='bgp_hijack' and x['observer']==o['id'])
    detail=await get('/detail',{'event_id':row['event_id']});assert detail['candidate']['expected_asn']==64501
    assert detail['previous_origin_announcement']['as_path']==[64500,64501]
    snap=json.loads(cache.values['noc:history:'+cursor]);snap['expires_at']=(datetime.now(UTC)-timedelta(seconds=1)).isoformat();cache.values['noc:history:'+cursor]=json.dumps(snap)
    await get('/history',params|{'cursor':cursor},409)
    await get('/history',{'resource':'bogus'},422);await get('/history',{},422)
    await get('/detail',{'event_id':str(uuid.uuid4())},404)
  checks.append('Actual SQL/HTTP history pins committed heads across delayed commit, pages without duplicates, filters mixed families correctly, detail preserves victim/prior path, expired/invalid pages fail explicitly')
  # Retention uses a real DELETE and a pinned page whose clock predates
  # cleanup. Configuration is supplied independently; all SQL remains real.
  class PolicyPool:
   def __getattr__(self,k):return getattr(pool,k)
   async def fetchrow(self,sql,*args):
    value=await pool.fetchrow(sql,*args)
    if 'FROM routing_diagnostic_policy' in sql:
     return {**dict(value),'enabled':True,'retention_hours':1,'max_bytes':2**30,'root_reserve':0}
    return value
  config_pool=PolicyPool();protected=uuid.uuid4();expired=uuid.uuid4();now=datetime.now(UTC)
  for ident,age in [(protected,62),(expired,66)]:
   await pool.execute("INSERT INTO routing_diagnostic_events(event_id,observer,sequence,ordinal,epoch,event_at,received_at,prefix,kind,involved_asns,evidence) VALUES($1,$2,1,$3,1,$4,$4,'198.19.0.0/24','retention_fixture',ARRAY[64500]::bigint[],'{}')",ident,o['id'],-int(age),now-timedelta(minutes=age))
  assert await maintenance(config_pool)
  assert await pool.fetchval('SELECT EXISTS(SELECT 1 FROM routing_diagnostic_events WHERE event_id=$1)',protected)
  assert not await pool.fetchval('SELECT EXISTS(SELECT 1 FROM routing_diagnostic_events WHERE event_id=$1)',expired)
  snap={'scope':{'asn':64500,'resource':None,'minutes':1440,'family':'all','collector':'all'},'as_of':(now-timedelta(minutes=3)).isoformat(),'expires_at':(now+timedelta(minutes=2)).isoformat(),'retention_hours':1,'heads':{o['id']:69,other['id']:2}}
  token=str(uuid.uuid4());cache.values['noc:history:'+token]=json.dumps(snap)
  outside=uuid.uuid4()
  await pool.execute("INSERT INTO routing_diagnostic_events(event_id,observer,sequence,ordinal,epoch,event_at,received_at,prefix,kind,involved_asns,evidence) VALUES($1,$2,1,-90,1,$3,$3,'198.19.1.0/24','retention_fixture',ARRAY[64500]::bigint[],'{}')",outside,o['id'],now-timedelta(minutes=90))
  with patch.object(api,'get_pg_pool',AsyncMock(return_value=pool)),patch.object(api,'get_redis',AsyncMock(return_value=cache)):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://qa') as client:
    response=await client.get('/diagnostics/history',params={'asn':64500,'minutes':1440,'cursor':token})
    assert response.status_code==200,response.text
    identifiers={r['event_id'] for r in response.json()['items']}
    assert str(protected) in identifiers and str(outside) not in identifiers
  checks.append('Retention DELETE protects an active page grace window; a later retention increase cannot widen its original snapshot')
  print(json.dumps({'pass':True,'checks':checks,'pinned_rows':n,'events':await pool.fetchval('SELECT count(*) FROM routing_diagnostic_events'),'checkpoint':await pool.fetchval('SELECT sequence FROM routing_diagnostic_cursors WHERE observer=$1',o['id'])},indent=2))
 finally:
  if pending is not None:
   await tx.rollback();await pool.release(pending)
  await pool.close()
asyncio.run(main())
