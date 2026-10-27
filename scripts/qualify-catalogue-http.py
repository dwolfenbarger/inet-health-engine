"""Real loopback HTTP + PostgreSQL qualification, isolated synthetic DEV data.

Only Redis/registry identity is a fixture; catalogue SQL, observers, evidence reads,
FastAPI validation and TCP HTTP are real. No production credentials or data used.
"""
import asyncio,json,os,socket,sys,time,uuid,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(ROOT/'collectors'),str(ROOT/'api')]
import asyncpg,httpx,uvicorn
from fastapi import FastAPI
from collectors.catalogue_incremental import install,bootstrap,maintain,finish_catchup,prune_versions
from api import routes_catalogue as api
from api.catalogue_backend import OFFSET
DB='inet_catalogue_lifecycle_qa'
class FixtureRedis:
 def __init__(self):self.values={}
 async def get(self,key):
  if key.startswith('inet:asn-identity:'):return json.dumps({'state':'fixture','holder':'Synthetic qualification ASN'})
  return self.values.get(key)
 async def setex(self,key,seconds,value):self.values[key]=value
async def main():
 assert socket.gethostname()=='inet-health-dev1'
 c=await asyncpg.connect(database=DB,user='postgres',host='/var/run/postgresql',command_timeout=30)
 assert await c.fetchval('SELECT current_database()')==DB
 await c.execute((ROOT/'docker/migrations/20261023-routing-state.sql').read_text())
 await c.execute((ROOT/'docker/migrations/20261027-catalogue-dirty.sql').read_text())
 await install(c)
 await c.execute('TRUNCATE routing_routes,routing_observers CASCADE')
 tables=await c.fetch("SELECT tablename FROM pg_tables WHERE schemaname='inet_incremental'")
 await c.execute('TRUNCATE '+','.join('inet_incremental.'+r['tablename'] for r in tables if r['tablename']!='runtime'))
 await c.execute('UPDATE inet_incremental.runtime SET initialized=false,reset_barrier=true')
 await c.execute('''CREATE TABLE IF NOT EXISTS source_evidence(source text,evidence_id text,payload jsonb,observed_at timestamptz);
 CREATE TABLE IF NOT EXISTS bgp_anomalies(time timestamptz,event_type text,affected_prefix cidr,origin_asn bigint,expected_asn bigint,severity text,confidence double precision,source text);
 TRUNCATE source_evidence,bgp_anomalies;
 GRANT SELECT ON source_evidence,bgp_anomalies TO inetuser;''')
 g=uuid.UUID(int=44)
 for n in [0,1]:
  obs='http-'+str(n)
  await c.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation,baseline_state) VALUES($1,'fixture',$1,$2::inet,$3,'mrt','{}',$4,'reconstructed')",obs,'192.0.2.'+str(n+1),64500+n,g)
  await c.execute("""INSERT INTO routing_routes SELECT $1,set_masklen('198.18.0.0'::inet+n,32)::cidr,1,$2,now(),2,'qa',true,64502,'[64500,64501,64502]','{}','http-proof' FROM generate_series(0,239) n""",obs,g)
  await c.execute("INSERT INTO routing_routes VALUES($1,'2001:db8::/32',1,$2,now(),2,'qa',true,4200000001,'[64500,{\"set\":[64505,64506]},4200000001]','{}','http-v6')",obs,g)
  await c.execute("INSERT INTO routing_routes VALUES($1,'198.18.0.0/24',1,$2,now(),2,'qa',true,64503,'[64500,64503]','{}','http-lpm')",obs,g)
 await c.execute("INSERT INTO source_evidence SELECT 'rpki-local',prefix::text||'|'||origin_asn::text,'{\"status\":\"unknown\",\"scope\":\"synthetic fixture\"}',now() FROM routing_routes GROUP BY prefix,origin_asn")
 await bootstrap(c)
 while await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty)'):await maintain(c,256)
 first=await finish_catchup(c);assert first
 # Legacy fixture proves default-off and pinned legacy generations still work.
 await c.execute('''CREATE SCHEMA IF NOT EXISTS inet_catalog;
 DROP TABLE IF EXISTS inet_catalog.generations,inet_catalog.routes,inet_catalog.memberships,inet_catalog.asns;
 CREATE TABLE inet_catalog.generations(id bigint PRIMARY KEY,captured_at timestamptz,published_at timestamptz,summary jsonb,observers jsonb);
 INSERT INTO inet_catalog.generations SELECT 42,captured_at,captured_at,summary,observers FROM inet_incremental.publications WHERE ready ORDER BY id DESC LIMIT 1;
 CREATE TABLE inet_catalog.routes AS SELECT 42::bigint generation,observer,prefix,origin_asn,peer_asn,collector,transit,members,family,event_at,path,attrs,evidence_id,rank FROM inet_incremental.current_routes;
 CREATE TABLE inet_catalog.memberships AS SELECT 42::bigint generation,asn,observer,prefix,roles FROM inet_incremental.current_memberships;
 CREATE TABLE inet_catalog.asns AS SELECT 42::bigint generation,asn,routes,prefixes,origins,transits,peers,ipv4,ipv6,observers FROM inet_incremental.asns WHERE asn>0;
 GRANT USAGE ON SCHEMA inet_catalog TO inetuser;
 GRANT SELECT ON ALL TABLES IN SCHEMA inet_catalog TO inetuser;''')
 async def role(conn):await conn.execute('SET ROLE inetuser')
 pool=await asyncpg.create_pool(database=DB,user='postgres',host='/var/run/postgresql',min_size=2,max_size=5,command_timeout=15,init=role)
 cache=FixtureRedis()
 async def get_pool():return pool
 async def get_redis():return cache
 api.get_pg_pool=get_pool;api.get_redis=get_redis
 os.environ['INET_INCREMENTAL_CATALOGUE']='1'
 app=FastAPI();app.include_router(api.router,prefix='/api/v1/noc')
 sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen();port=sock.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
 task=asyncio.create_task(server.serve(sockets=[sock]))
 while not server.started:
  if task.done():await task
  await asyncio.sleep(.01)
 timings=[];checks=[]
 try:
  async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}/api/v1/noc/catalogue',timeout=20) as client:
   async def get(path,params=None,status=200):
    at=time.monotonic();r=await client.get(path,params=params);timings.append(time.monotonic()-at)
    assert r.status_code==status,(path,params,r.status_code,r.text[:300])
    return r.json()
   directory=await get('/asns',{'limit':2});gen=directory['generation']['id'];assert gen==OFFSET+first
   asns=[];page=directory
   while True:
    asns.extend(r['asn'] for r in page['asns'])
    if page['next_after'] is None:break
    page=await get('/asns',{'limit':2,'after':page['next_after'],'generation_id':gen})
   assert len(asns)==len(set(asns))==directory['total'] and 0 not in asns and 64505 not in asns
   for asn in asns:
    detail=await get('/asns/'+str(asn),{'generation_id':gen});assert detail['observed']
    for role,column in [('any','routes'),('origin','origins'),('transit','transits'),('peer','peers')]:
     page=await get('/routes',{'asn':asn,'role':role,'generation_id':gen,'limit':5})
     assert page['total']==detail['inventory'][column]
     if role!='any':assert all(r['roles'][role] for r in page['routes'])
   checks.append('all ASN pages, four roles, four-byte ASN and AS_SET exclusion')
   for family in ['all','4','6']:
    for collector in ['all','http-0','http-1','absent']:
     page=await get('/routes',{'family':family,'collector':collector,'generation_id':gen,'limit':5})
     expected=await c.fetchval('SELECT count(*) FROM inet_incremental.routes_at($1) WHERE ($2=\'all\' OR family::text=$2) AND ($3=\'all\' OR collector=$3)',first,family,collector)
     assert page['total']==expected
   checks.append('global, collector and family counts reconcile')
   expected=await c.fetch('SELECT r.observer,r.prefix::text prefix FROM inet_incremental.routes_at($1) r WHERE origin_asn=64502 ORDER BY r.observer,r.prefix',first)
   params={'asn':64502,'role':'origin','limit':17,'generation_id':gen};page=await get('/routes',params);cursor=page['next_cursor'];seen=[(r['observer'],r['prefix']) for r in page['routes']]
   # Mutate + publish during browsing; every subsequent page must remain pinned.
   await c.execute("UPDATE routing_routes SET present=false WHERE observer='http-0' AND prefix='198.18.0.0/32'")
   result=await maintain(c,256);assert result['changed']==result['withdrawn']==1
   new=await finish_catchup(c);assert new>first
   while cursor:
    page=await get('/routes',dict(params,cursor=cursor));seen.extend((r['observer'],r['prefix']) for r in page['routes']);cursor=page['next_cursor']
   assert seen==[(r['observer'],r['prefix']) for r in expected] and len(seen)==len(set(seen))
   checks.append('all route pages pinned across concurrent withdrawal/publication, no duplicate or missing key')
   prefix=await get('/routes',{'prefix':'198.18.0.0/32','generation_id':gen});assert prefix['total']==2
   lpm=await get('/routes',{'ip':'198.18.0.2','generation_id':gen});assert lpm['total']==2 and all(r['prefix']=='198.18.0.2/32' for r in lpm['routes'])
   v6=await get('/routes',{'ip':'2001:db8::1','generation_id':gen});assert all(r['as_path']==[] and r['path_segments'][1]=={'set':[64505,64506]} for r in v6['routes'])
   for p in [{'cursor':'bad'},{'ip':'invalid'},{'ip':'1.1.1.1','prefix':'1.1.1.0/24'},{'asn':0},{'limit':201}]:await get('/routes',p,422)
   await get('/routes',dict(params,cursor=(await get('/routes',params))['next_cursor'],role='transit'),422)
   await get('/routes',{'generation_id':OFFSET+999999999},409)
   await get('/asns',{'q':'bad'},422)
   absent=await get('/asns/4294967295');assert not absent['observed']
   checks.append('prefix, longest-prefix IPv4/IPv6, validation and explicit expiry')
   # Stale and reset status must remain visible, with old publication readable.
   await c.execute('UPDATE inet_incremental.runtime SET reset_barrier=true')
   assert (await get('/asns'))['generation']['reset_pending']
   await c.execute('UPDATE inet_incremental.runtime SET reset_barrier=false')
   await c.execute("UPDATE inet_incremental.publications SET captured_at=now()-interval '3 minutes' WHERE id=$1",new)
   assert (await get('/asns'))['generation']['stale']
   summary=await c.fetchval('SELECT summary FROM inet_incremental.publications WHERE id=$1',new)
   await c.execute("UPDATE inet_incremental.publications SET summary='{}' WHERE id=$1",new)
   await get('/asns',status=503)
   await c.execute('UPDATE inet_incremental.publications SET summary=$2::jsonb WHERE id=$1',new,summary)
   os.environ['INET_INCREMENTAL_CATALOGUE']='0'
   assert (await get('/asns'))['generation']['id']==42
   await get('/asns',{'generation_id':gen},409)
   os.environ['INET_INCREMENTAL_CATALOGUE']='1'
   assert (await get('/asns',{'generation_id':42}))['generation']['id']==42
   checks.append('stale/reset flags, incomplete503, default-off legacy and pinned legacy support')
   # More expired metadata than the delete budget: remaining tokens still own history.
   for _ in range(4):await finish_catchup(c)
   latest=await c.fetchval('SELECT max(id) FROM inet_incremental.publications')
   await c.execute("UPDATE inet_incremental.publications SET captured_at=now()-interval '2 hours' WHERE id<$1",latest)
   await prune_versions(c,60,1)
   if not await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.publications WHERE id=$1)',first):
    await get('/routes',{'generation_id':gen},409)
   for r in await c.fetch('SELECT id FROM inet_incremental.publications WHERE ready AND id<$1',latest):
    body=await get('/routes',{'generation_id':OFFSET+r['id']})
    assert body['total']==await c.fetchval('SELECT count(*) FROM inet_incremental.routes_at($1)',r['id'])
   # Retain last successful view even when all publication ages exceed the goal.
   await c.execute("UPDATE inet_incremental.publications SET captured_at=now()-interval '2 hours'")
   for _ in range(12):await prune_versions(c,60,1)
   assert (await get('/asns'))['generation']['id']==OFFSET+latest
   checks.append('bounded cleanup preserves every remaining token and last ready view')
   report={'pass':True,'http_requests':len(timings),'p95_seconds':round(sorted(timings)[int(.95*(len(timings)-1))],4),'max_seconds':round(max(timings),4),'checks':checks,'scope':'synthetic DEV, real TCP HTTP and PostgreSQL application role; registry identity/Redis fixture'}
   Path('/tmp/inet-catalogue-http.json').write_text(json.dumps(report,indent=2));print('PASS_HTTP',json.dumps(report),flush=True)
 finally:
  server.should_exit=True;await task;await pool.close();await c.close()
asyncio.run(main())
