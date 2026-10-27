"""Read-only public catalogue HTTP acceptance against generation-pinned SQL."""
import asyncio,json,os,sys,time
from pathlib import Path
sys.path[:0]=['/opt/inet-journal-runtime/vendor','/home/david/inet-health-engine/api']
import asyncpg,httpx
from api.catalogue_backend import for_generation,OFFSET
async def main():
 c=await asyncpg.connect(database='inethealth',user='postgres',host='/var/run/postgresql',command_timeout=30)
 await c.execute("SET statement_timeout='25s'; SET lock_timeout='500ms'; SET default_transaction_read_only=on; SET work_mem='32MB'; SET temp_file_limit='1GB'; SET jit=off")
 report={'scope':'native public catalogue complete HTTP navigation','checks':[],'timings':[]}
 # HTTP queries are generation-pinned; don't hold source locks across navigation.
 os.environ['INET_INCREMENTAL_CATALOGUE']='1'
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8000/api/v1/noc/catalogue',timeout=30) as client:
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
  lpm=await get('/routes',{'ip':'1.1.1.1','generation_id':g['id']})
  expected_lpm=await scoped.fetch("SELECT DISTINCT ON (observer) observer,prefix::text prefix FROM inet_catalog.routes r WHERE generation=$1 AND prefix>>='1.1.1.1'::inet ORDER BY r.observer,masklen(r.prefix) DESC,r.prefix",g['id'])
  assert [(r['observer'],r['prefix']) for r in lpm['routes']]==[(r['observer'],r['prefix']) for r in expected_lpm] and expected_lpm
  v6=await get('/routes',{'family':'6','generation_id':g['id']});assert v6['routes'] and all(':' in r['prefix'] for r in v6['routes'])
 report['pass']=True;Path('/tmp/inet-catalogue-public-qualification.json').write_text(json.dumps(report,indent=2,default=str))
 times=[r['seconds'] for r in report['timings'] if 'path' in r]
 print('PASS_PUBLIC_CATALOGUE',json.dumps({'generation':report['generation'],'http_requests':len(times),'p95_seconds':sorted(times)[int(.95*(len(times)-1))],'max_seconds':max(times),'checks':report['checks']}),flush=True)
 await c.close()
asyncio.run(main())
