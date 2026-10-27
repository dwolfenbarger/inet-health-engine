"""Independent-connection bootstrap/reset tests, private isolated DEV database."""
import asyncio,json,sys,time,uuid,os
from pathlib import Path
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(Path(__file__).resolve().parents[1]/'collectors')]
import asyncpg
from collectors.catalogue_incremental import install,bootstrap,maintain,reconcile_reset,finish_catchup,CURRENT,FIELDS
ROOT=Path(__file__).resolve().parents[1]
async def main():
 c=await asyncpg.connect(database='inet_catalogue_lifecycle_qa',user='postgres',host='/var/run/postgresql',command_timeout=15)
 w=await asyncpg.connect(database='inet_catalogue_lifecycle_qa',user='postgres',host='/var/run/postgresql',command_timeout=3)
 assert await c.fetchval('SELECT current_database()')=='inet_catalogue_lifecycle_qa'
 await c.execute((ROOT/'docker/migrations/20261023-routing-state.sql').read_text())
 await c.execute((ROOT/'docker/migrations/20261027-catalogue-dirty.sql').read_text())
 await install(c)
 await c.execute('TRUNCATE routing_routes,routing_observers CASCADE')
 for table in ['current_routes','current_memberships','asns','prefix_refs','observer_refs','publications','current_routes_history','current_memberships_history','asns_history','origin_refs','facets','facets_history']:
  await c.execute('TRUNCATE inet_incremental.'+table)
 await c.execute('TRUNCATE inet_incremental.origin_refs,inet_incremental.facets,inet_incremental.facets_history')
 await c.execute('UPDATE inet_incremental.runtime SET initialized=false,reset_barrier=true')
 g=uuid.UUID(int=1)
 await c.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation) VALUES('lifecycle','fixture','fixture','192.0.2.1',64500,'mrt','{}',$1)",g)
 await c.execute("INSERT INTO routing_routes VALUES('lifecycle','192.0.2.0/24',1,$1,now(),2,'qa',true,64501,'[64500,64501]','{}','fixture')",g)
 async def writer():
  at=time.monotonic()
  async with w.transaction():
   await w.fetchrow("SELECT id FROM routing_observers WHERE id='lifecycle' FOR UPDATE")
   await w.execute("UPDATE routing_routes SET origin_asn=64502,path='[64500,64502]',event_at=event_at+interval '1 second' WHERE observer='lifecycle'")
   await w.execute("INSERT INTO routing_routes VALUES('lifecycle','198.51.100.0/24',1,$1,now(),2,'qa',true,64503,'[64500,64503]','{}','fixture') ON CONFLICT DO NOTHING",g)
  return time.monotonic()-at
 async def failed_writer():
  await writer();raise RuntimeError('Injected bootstrap interruption')
 try:await bootstrap(c,failed_writer)
 except RuntimeError as e:assert 'Injected' in str(e)
 else:raise AssertionError('Expected injected failure')
 assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.current_routes)')
 assert await c.fetchval('SELECT count(*) FROM inet_incremental.dirty')==2
 # Change again AFTER route snapshot: it must survive as catch-up work.
 async def overlap():
  async with w.transaction():
   await w.fetchrow("SELECT id FROM routing_observers WHERE id='lifecycle' FOR UPDATE")
   await w.execute("UPDATE routing_routes SET origin_asn=64504,path='[64500,64504]',event_at=event_at+interval '1 second' WHERE prefix='192.0.2.0/24'")
 before=time.monotonic();result=await bootstrap(c,overlap)
 assert await c.fetchval("SELECT origin_asn FROM inet_incremental.current_routes WHERE prefix='192.0.2.0/24'")==64502
 assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.publications WHERE ready)')
 while await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty)'):await maintain(c,1)
 ready=await finish_catchup(c);assert ready
 sys.path.insert(0,str(ROOT/'api'));os.environ['INET_INCREMENTAL_CATALOGUE']='1'
 from api.catalogue_backend import selected_generation,for_generation,retained,OFFSET
 metadata=await selected_generation(c)
 assert metadata['summary']['routes']==2 and metadata['summary']['prefixes']==2 and metadata['summary']['origins']==2
 scoped=for_generation(c,metadata)
 assert await scoped.fetchval('SELECT count(*) FROM inet_catalog.routes WHERE generation=$1',metadata['id'])==2
 assert await scoped.fetchval('SELECT count(*) FROM inet_catalog.memberships WHERE generation=$1 AND asn=64500',metadata['id'])==2
 assert await scoped.fetchval('SELECT routes FROM inet_catalog.asns WHERE generation=$1 AND asn=64500',metadata['id'])==2
 assert await retained(c,metadata['id'])
 async with c.transaction():
  await c.execute('SET LOCAL ROLE inetuser')
  role_metadata=await selected_generation(c,metadata['id'])
  assert await for_generation(c,role_metadata).fetchval('SELECT count(*) FROM inet_catalog.routes WHERE generation=$1',metadata['id'])==2
 assert await c.fetchval("SELECT origin_asn FROM inet_incremental.current_routes WHERE prefix='192.0.2.0/24'")==64504
 assert await c.fetchval("SELECT origin_asn FROM inet_incremental.routes_at($1) WHERE prefix='192.0.2.0/24'",result['version'])==64502
 # A session reset must also remove source keys physically deleted meanwhile.
 async with w.transaction():
  await w.fetchrow("SELECT id FROM routing_observers WHERE id='lifecycle' FOR UPDATE")
  await w.execute("UPDATE routing_observers SET epoch=2,generation=$1 WHERE id='lifecycle'",uuid.UUID(int=2))
  await w.execute("DELETE FROM routing_routes WHERE prefix='198.51.100.0/24'")
 assert not await finish_catchup(c)
 batches=0
 while await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.resets)'):
  r=await reconcile_reset(c,1);assert r['queued']<=1;batches+=1
  if batches==1:
   await w.execute("UPDATE routing_observers SET epoch=3,generation=$1 WHERE id='lifecycle'",uuid.UUID(int=3))
  assert batches<12
 while await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty)'):await maintain(c,1)
 assert await finish_catchup(c)
 pinned_metadata=await selected_generation(c,OFFSET+ready)
 assert await for_generation(c,pinned_metadata).fetchval('SELECT count(*) FROM inet_catalog.routes WHERE generation=$1',pinned_metadata['id'])==2
 assert (await selected_generation(c))['summary']['routes']==0
 assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.current_routes)')
 assert await c.fetchval('SELECT count(*) FROM inet_incremental.routes_at($1)',ready)==2
 assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.asns WHERE asn>0)')
 print('PASS_BOOTSTRAP_RESET',json.dumps({'nonblocking_overlap':True,'injected_failure_preserves_queue':True,'snapshot_plus_catchup':True,'revision_restart_batches':batches,'old_pinned_routes_retained':True,'seconds':round(time.monotonic()-before,3)}))
 await w.close();await c.close()
asyncio.run(main())
