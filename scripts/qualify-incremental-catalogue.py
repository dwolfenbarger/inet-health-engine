"""Isolated PostgreSQL integration tests; never operates in the live database."""
import asyncio,json,sys,uuid,random,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(Path(__file__).resolve().parents[1]/'collectors')]
import asyncpg
from collectors.catalogue_incremental import install,seed,maintain,CURRENT,FIELDS,prune_versions,snapshot
ROOT=Path(__file__).resolve().parents[1]
async def main():
 c=await asyncpg.connect(database='inet_catalogue_qa',user='postgres',host='/var/run/postgresql',command_timeout=30)
 assert await c.fetchval('SELECT current_database()')=='inet_catalogue_qa'
 await c.execute((ROOT/'docker/migrations/20261023-routing-state.sql').read_text())
 await c.execute((ROOT/'docker/migrations/20261027-catalogue-dirty.sql').read_text())
 await install(c)
 t=c.transaction();await t.start()
 try:
  g=uuid.UUID(int=1);at=datetime(2026,1,1,tzinfo=timezone.utc)
  for name,peer in [('qa-a',64500),('qa-b',64501)]:
   await c.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation) VALUES($1,'fixture',$1,$2::inet,$3,'mrt','{}',$4)",name,'192.0.2.'+str(peer-64499),peer,g)
  async def put(obs,prefix,origin,path,present=True):
   await c.execute('''INSERT INTO routing_routes VALUES($1,$2::text::cidr,1,$3,$4,2,'qa',$5,$6,$7::jsonb,'{}','fixture')
   ON CONFLICT(observer,prefix) DO UPDATE SET present=excluded.present,origin_asn=excluded.origin_asn,path=excluded.path,event_at=routing_routes.event_at+interval '1 second' ''',obs,prefix,g,at,present,origin,json.dumps(path))
  async def exact():
   expected=await c.fetch(CURRENT+' ORDER BY r.observer,r.prefix')
   actual=await c.fetch('SELECT '+FIELDS+' FROM inet_incremental.current_routes ORDER BY observer,prefix')
   assert [dict(r) for r in actual]==[dict(r) for r in expected],'Current route mismatch'
   em=[]
   for r in expected:
    for a in r['members']:em.append((a,r['observer'],str(r['prefix']),(int(r['origin_asn']==a)+2*int(a in r['transit'])+4*int(r['peer_asn']==a))))
   am=await c.fetch('SELECT asn,observer,prefix::text,roles FROM inet_incremental.current_memberships')
   assert sorted(em)==sorted(tuple(r) for r in am),'ASN membership/role mismatch'
   counts=await c.fetch('''SELECT asn,count(*) routes,count(DISTINCT prefix) prefixes,count(*) FILTER(WHERE (roles&1)<>0) origins,count(*) FILTER(WHERE (roles&2)<>0) transits,count(*) FILTER(WHERE (roles&4)<>0) peers,count(*) FILTER(WHERE family(prefix)=4) ipv4,count(*) FILTER(WHERE family(prefix)=6) ipv6,count(DISTINCT observer) observers FROM inet_incremental.current_memberships GROUP BY asn ORDER BY asn''')
   stored=await c.fetch('SELECT asn,routes,prefixes,origins,transits,peers,ipv4,ipv6,observers FROM inet_incremental.asns WHERE asn>0 ORDER BY asn')
   assert [dict(r) for r in counts]==[dict(r) for r in stored],'Aggregate or distinct counts differ'
   expected_global=await c.fetchrow('SELECT count(*) routes,count(DISTINCT prefix) prefixes,count(DISTINCT origin_asn) origins,count(DISTINCT observer) observers,count(*) FILTER(WHERE family=4) ipv4,count(*) FILTER(WHERE family=6) ipv6 FROM inet_incremental.current_routes')
   actual_global=await c.fetchrow('SELECT routes,prefixes,origins,observers,ipv4,ipv6 FROM inet_incremental.asns WHERE asn=0')
   assert dict(expected_global)==(dict(actual_global) if actual_global else dict.fromkeys(expected_global.keys(),0)),'Global summary differs'
   expected_facets=await c.fetch('SELECT observer,collector,family,count(*) routes FROM inet_incremental.current_routes GROUP BY observer,collector,family ORDER BY observer,family')
   actual_facets=await c.fetch('SELECT observer,collector,family,routes FROM inet_incremental.facets ORDER BY observer,family')
   assert [dict(r) for r in expected_facets]==[dict(r) for r in actual_facets],'Facet summary differs'
  for i in range(256):await put('qa-a',f'198.18.{i}.0/24',64502,[64500,64503,64502])
  await put('qa-b','198.18.0.0/24',64502,[64501,64502]);await put('qa-a','2001:db8::/32',4200000001,[64500,{'set':[64504,64505]},4200000001])
  await seed(c);await exact()
  pinned=await c.fetchval('SELECT max(id) FROM inet_incremental.publications')
  retained=await c.fetch('SELECT * FROM inet_incremental.routes_at($1) ORDER BY observer,prefix',pinned)
  retained_counts=await c.fetch('SELECT * FROM inet_incremental.asns_at($1) ORDER BY asn',pinned)
  rng=random.Random(123);changes=0;bench=[]
  for cycle in range(8):
   for i in range(70):
    n=rng.randrange(256);await put('qa-a',f'198.18.{n}.0/24',64502+cycle,[64500,64510+cycle,64502+cycle],bool(rng.randrange(2)));changes+=1
   count=await c.fetchval('SELECT count(*) FROM inet_incremental.dirty');assert count<=70
   measure=c.transaction();await measure.start()
   wal=await c.fetchval('SELECT pg_current_wal_insert_lsn()::text');started=time.monotonic()
   await seed(c)
   full_seconds=time.monotonic()-started
   full_wal=await c.fetchval('SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),$1::text::pg_lsn)',wal)
   await measure.rollback()
   sub=c.transaction();await sub.start();await maintain(c,17);await sub.rollback()
   assert await c.fetchval('SELECT count(*) FROM inet_incremental.dirty')==count,'Rollback lost work'
   wal=await c.fetchval('SELECT pg_current_wal_insert_lsn()::text');started=time.monotonic()
   while await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty)'):
    result=await maintain(c,17);assert result['processed']<=17
   bench.append({'full_wal':int(full_wal),'incremental_wal':int(await c.fetchval('SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),$1::text::pg_lsn)',wal)),'full_seconds':full_seconds,'incremental_seconds':time.monotonic()-started})
   await exact()
   assert await c.fetch('SELECT * FROM inet_incremental.routes_at($1) ORDER BY observer,prefix',pinned)==retained,'Pinned routes changed'
   assert await c.fetch('SELECT * FROM inet_incremental.asns_at($1) ORDER BY asn',pinned)==retained_counts,'Pinned counts changed'
  await c.execute("UPDATE inet_incremental.publications SET captured_at=now()-interval '2 hours' WHERE id=$1",pinned)
  await prune_versions(c,3600,17)
  try:await snapshot(c,pinned)
  except LookupError:pass
  else:raise AssertionError('Expired token remained readable')
  await exact()
  # A rolled-back writer must leave neither route nor dirty-key mutation.
  sub=c.transaction();await sub.start();await put('qa-a','203.0.113.0/24',64555,[64500,64555]);await sub.rollback()
  assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty WHERE prefix='203.0.113.0/24')")
  await c.execute("UPDATE routing_observers SET epoch=epoch+1,generation=$1 WHERE id='qa-a'",uuid.UUID(int=2))
  try:await maintain(c)
  except RuntimeError as e:assert 'reset' in str(e)
  else:raise AssertionError('Reset silently published')
  await seed(c);await exact()
  await c.execute('TRUNCATE routing_routes')
  try:await maintain(c)
  except RuntimeError as e:assert 'reset' in str(e)
  else:raise AssertionError('Truncate bypassed reset barrier')
  print('PASS',json.dumps({'mutations':changes,'fixture_routes':258,'cycles':8,'batch_limit':17,'rollback_and_reset_guards':True,'public_serving_enabled':False,'benchmark':bench}))
 finally:await t.rollback()
 await concurrency(c)
 await c.close()

async def concurrency(c):
 # Dedicated committed fixture is necessary to exercise independent connections.
 other=await asyncpg.connect(database='inet_catalogue_qa',user='postgres',host='/var/run/postgresql',command_timeout=10)
 g=uuid.UUID(int=9);at=datetime(2026,1,1,tzinfo=timezone.utc)
 await c.execute("DELETE FROM routing_routes WHERE observer='qa-concurrent'")
 await c.execute("DELETE FROM routing_observers WHERE id='qa-concurrent'")
 await c.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation) VALUES('qa-concurrent','fixture','qa-concurrent','192.0.2.99',64500,'mrt','{}',$1)",g)
 await c.execute("INSERT INTO routing_routes VALUES('qa-concurrent','203.0.113.0/24',1,$1,$2,2,'qa',true,64501,'[64500,64501]','{}','fixture')",g,at)
 await seed(c)
 old=await c.fetchval('SELECT max(id) FROM inet_incremental.publications')
 t=other.transaction();await t.start()
 try:
  await other.fetchrow("SELECT id FROM routing_observers WHERE id='qa-concurrent' FOR UPDATE")
  await other.execute("UPDATE routing_routes SET origin_asn=64502,path='[64500,64502]',event_at=event_at+interval '1 second' WHERE observer='qa-concurrent'")
  worker=asyncio.create_task(maintain(c,1));await asyncio.sleep(.1)
  # Dirty is not visible until writer commit; worker may legitimately do no work.
  if not worker.done():
   await t.commit();await asyncio.wait_for(worker,5)
  else:
   await worker;await t.commit()
  await maintain(c,1)
  assert await c.fetchval("SELECT origin_asn FROM inet_incremental.current_routes WHERE observer='qa-concurrent'")==64502
  assert await c.fetchval("SELECT origin_asn FROM inet_incremental.routes_at($1) WHERE observer='qa-concurrent'",old)==64501
  assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty)')
  # Hold a committed dirty key behind the same source lock: consumer must wait.
  await other.execute("UPDATE routing_routes SET event_at=event_at+interval '1 second' WHERE observer='qa-concurrent'")
  t=other.transaction();await t.start()
  await other.fetchrow("SELECT id FROM routing_observers WHERE id='qa-concurrent' FOR UPDATE")
  worker=asyncio.create_task(maintain(c,1));await asyncio.sleep(.1);assert not worker.done(),'Consumer bypassed source lock'
  await t.commit();await asyncio.wait_for(worker,5)
  print('PASS_TWO_CONNECTION_COMMIT_AND_LOCK_ORDER')
 finally:
  if other.is_in_transaction():await t.rollback()
  await other.close()

asyncio.run(main())
