"""Read-only source/derived parity and private loopback HTTP qualification."""
import asyncio,json,sys,time,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(ROOT/'api')]
import asyncpg,httpx
from api.catalogue_backend import for_generation,OFFSET
async def main():
 c=await asyncpg.connect(database='inethealth',user='postgres',host='/var/run/postgresql',command_timeout=180)
 await c.execute("SET statement_timeout='170s'; SET lock_timeout='500ms'; SET default_transaction_read_only=on; SET work_mem='32MB'; SET temp_file_limit='4GB'; SET jit=off")
 report={'scope':'read-only production shadow parity and private loopback HTTP; public backend unchanged','checks':[],'timings':[]}
 # One MVCC snapshot makes source changes and their dirty capture atomic. Pending
 # keys are explicitly excluded, not classified as matched or silently healthy.
 async with c.transaction(isolation='repeatable_read',readonly=True):
  state=await c.fetchrow('SELECT initialized,reset_barrier FROM inet_incremental.runtime WHERE singleton')
  assert state and state['initialized'] and not state['reset_barrier']
  assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.resets)')
  pending=await c.fetchval('SELECT count(*) FROM inet_incremental.dirty');report['excluded_pending_keys']=pending
  at=time.monotonic()
  mismatch=await c.fetchrow('''WITH source AS (
    SELECT r.observer,r.prefix,r.origin_asn,o.peer_asn,o.collector,family(r.prefix)::smallint family,r.event_at,r.path,r.attrs,r.evidence_id,r.rank
    FROM routing_routes r JOIN routing_observers o ON o.id=r.observer WHERE r.present AND r.epoch=o.epoch AND r.generation=o.generation)
   SELECT coalesce(s.observer,d.observer) observer,coalesce(s.prefix,d.prefix)::text prefix
   FROM source s FULL JOIN inet_incremental.current_routes d USING(observer,prefix)
   WHERE NOT EXISTS(SELECT 1 FROM inet_incremental.dirty q WHERE q.observer=coalesce(s.observer,d.observer) AND q.prefix=coalesce(s.prefix,d.prefix))
    AND (s.observer IS NULL OR d.observer IS NULL OR
    (s.origin_asn,s.peer_asn,s.collector,s.family,s.event_at,s.path,s.attrs,s.evidence_id,s.rank)
      IS DISTINCT FROM (d.origin_asn,d.peer_asn,d.collector,d.family,d.event_at,d.path,d.attrs,d.evidence_id,d.rank)) LIMIT 1''')
  assert mismatch is None,dict(mismatch) if mismatch else None
  report['timings'].append({'check':'all_source_keys_and_raw_fields_excluding_pending','seconds':time.monotonic()-at});report['checks'].append('all source route keys/raw fields match except explicitly queued keys')
  at=time.monotonic()
  mismatch=await c.fetchrow('''WITH expected AS (
    SELECT asn,count(*) routes,count(DISTINCT prefix) prefixes,count(*) FILTER(WHERE (roles&1)<>0) origins,count(*) FILTER(WHERE (roles&2)<>0) transits,count(*) FILTER(WHERE (roles&4)<>0) peers,count(*) FILTER(WHERE family(prefix)=4) ipv4,count(*) FILTER(WHERE family(prefix)=6) ipv6,count(DISTINCT observer) observers
    FROM inet_incremental.current_memberships GROUP BY asn)
   SELECT coalesce(e.asn,a.asn) asn FROM expected e FULL JOIN (SELECT * FROM inet_incremental.asns WHERE asn>0) a USING(asn)
   WHERE e.asn IS NULL OR a.asn IS NULL OR (e.routes,e.prefixes,e.origins,e.transits,e.peers,e.ipv4,e.ipv6,e.observers) IS DISTINCT FROM (a.routes,a.prefixes,a.origins,a.transits,a.peers,a.ipv4,a.ipv6,a.observers) LIMIT 1''')
  assert mismatch is None,dict(mismatch) if mismatch else None
  report['timings'].append({'check':'all_asn_counts','seconds':time.monotonic()-at});report['checks'].append('all ASN route/role/family/distinct counts reconcile')
  expected=await c.fetchrow('SELECT count(*) routes,count(DISTINCT prefix) prefixes,count(DISTINCT origin_asn) origins,count(DISTINCT observer) observers FROM inet_incremental.current_routes')
  actual=await c.fetchrow('SELECT routes,prefixes,origins,observers FROM inet_incremental.asns WHERE asn=0')
  assert dict(expected)==dict(actual);report['global_counts']=dict(actual);report['checks'].append('all global counts reconcile')
 # HTTP queries are generation-pinned; don't hold source locks across navigation.
 os.environ['INET_INCREMENTAL_CATALOGUE']='1'
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8012/api/v1/noc/catalogue',timeout=30) as client:
  async def get(path,params=None,status=200):
   at=time.monotonic();r=await client.get(path,params=params);report['timings'].append({'path':path,'seconds':time.monotonic()-at})
   assert r.status_code==status,(path,params,r.status_code,r.text[:200]);return r.json()
  directory=await get('/asns',{'limit':100});g=directory['generation'];assert g['id']>=OFFSET and g['backend']=='incremental'
  report['generation']=g['id'];scoped=for_generation(c,g)
  asns=[];page=directory
  while True:
   asns.extend(r['asn'] for r in page['asns'])
   if page['next_after'] is None:break
   page=await get('/asns',{'limit':100,'generation_id':g['id'],'after':page['next_after']})
  assert len(asns)==len(set(asns))==directory['total']==g['summary']['asns'] and 0 not in asns
  report['checks'].append('all ASN directory pages reconcile without duplicates/ASN0')
  for asn in [13335,3356,1299,174,6939,15169,16509,2497,28624,133210,15547,30844]:
   detail=await get('/asns/'+str(asn),{'generation_id':g['id']});assert detail['observed']
   for role,column in [('any','routes'),('origin','origins'),('transit','transits'),('peer','peers')]:
    p=await get('/routes',{'asn':asn,'role':role,'generation_id':g['id'],'limit':10});assert p['total']==detail['inventory'][column]
    if role!='any':assert all(r['roles'][role] for r in p['routes'])
  expected=await scoped.fetch('SELECT observer,prefix::text prefix FROM inet_catalog.routes r WHERE generation=$1 AND origin_asn=13335 ORDER BY r.observer,r.prefix',g['id'])
  seen=[];cursor=None
  while True:
   params={'asn':13335,'role':'origin','generation_id':g['id'],'limit':200}
   if cursor:params['cursor']=cursor
   page=await get('/routes',params);seen.extend((r['observer'],r['prefix']) for r in page['routes']);cursor=page['next_cursor']
   if not cursor:break
  assert seen==[(r['observer'],r['prefix']) for r in expected] and len(seen)==len(set(seen))
  report['checks'].append('all AS13335 origin pages reconcile while intake/maintenance continue')
  await get('/routes',{'cursor':'invalid'},422);await get('/routes',{'generation_id':OFFSET+999999999},409)
  lpm=await get('/routes',{'ip':'1.1.1.1','generation_id':g['id']});assert lpm['routes'] and all(r['prefix']=='1.1.1.0/24' for r in lpm['routes'])
  v6=await get('/routes',{'family':'6','generation_id':g['id']});assert v6['routes'] and all(':' in r['prefix'] for r in v6['routes'])
 report['pass']=True;Path('/tmp/inet-catalogue-live-qualification.json').write_text(json.dumps(report,indent=2,default=str))
 times=[r['seconds'] for r in report['timings'] if 'path' in r];print('PASS_LIVE_CATALOGUE',json.dumps({'generation':report['generation'],'http_requests':len(times),'p95_seconds':sorted(times)[int(.95*(len(times)-1))],'max_seconds':max(times),'checks':report['checks'],'global_counts':report['global_counts'],'excluded_pending_keys':pending}),flush=True)
 await c.close()
asyncio.run(main())
