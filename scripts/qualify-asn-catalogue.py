"""Read-only qualification against a published candidate and its own immutable scope."""
import asyncio,json,sys,time,statistics
from pathlib import Path
sys.path.insert(0,'/opt/inet-journal-runtime/vendor')
import asyncpg
from contextlib import AsyncExitStack
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
async def main():
 c=await asyncpg.connect(database='inethealth',user='postgres',host='/var/run/postgresql',command_timeout=90)
 report={'checks':[],'timings':[]};started=time.monotonic()
 async with AsyncExitStack():
  async def get(path,params=None,status=200):
   def request():
    try:r=urlopen('http://127.0.0.1:8011/api/v1/noc/catalogue'+path+'?'+urlencode(params or {}),timeout=30)
    except HTTPError as e:r=e
    with r:return r.status,r.read()
   t=time.monotonic();code,body=await asyncio.to_thread(request);elapsed=round(time.monotonic()-t,3)
   if code!=status:raise AssertionError((path,params,code,body[:300]))
   report['timings'].append({'path':path,'seconds':elapsed});return json.loads(body)
  directory=await get('/asns',{'limit':100});g=directory['generation']['id'];report['generation']=g;report['summary']=directory['generation']['summary']
  expected=await c.fetchval('SELECT count(*) FROM inet_catalog.asns WHERE generation=$1',g)
  seen=[];page=directory
  while True:
   seen.extend(x['asn'] for x in page['asns'])
   if page['next_after'] is None:break
   page=await get('/asns',{'limit':100,'generation_id':g,'after':page['next_after']})
  assert len(seen)==len(set(seen))==expected==directory['total'];print('DIRECTORY',expected,flush=True);report['checks'].append(f'all directory pages reconcile: {expected}')
  chosen=await c.fetch('SELECT asn,routes,transits FROM inet_catalog.asns WHERE generation=$1 AND asn=ANY($2::bigint[]) ORDER BY asn',g,[13335,3356,1299,174,6939,15169,16509,2497,28624,133210,15547,30844])
  assert len(chosen)>=10;report['asns']=[]
  quiet=await c.fetchval("SELECT asn FROM inet_catalog.asns WHERE generation=$1 AND origins>0 AND NOT EXISTS (SELECT 1 FROM bgp_updates b WHERE b.origin_asn=asn AND b.time>now()-interval '15 minutes') ORDER BY asn LIMIT 1",g)
  assert quiet is not None
  quiet_result=await get('/asns',{'q':str(quiet),'generation_id':g});assert quiet_result['total']==1
  report['checks'].append(f'quiet ASN discoverable without recent sampled activity: AS{quiet}')
  for row in chosen:
   asn=row['asn'];detail=await get('/asns/'+str(asn),{'generation_id':g})
   assert detail['observed'] and detail['inventory']['routes']==row['routes']
   for role in ['any','origin','transit','peer']:
    p=await get('/routes',{'asn':asn,'role':role,'generation_id':g,'limit':10})
    count=detail['inventory'][{'any':'routes','origin':'origins','transit':'transits','peer':'peers'}[role]]
    assert p['total']==count
    if role!='any':assert all(x['roles'][role] for x in p['routes'])
   report['asns'].append({'asn':asn,'routes':row['routes'],'holder':detail['metadata'].get('holder'),'identity_state':detail['metadata']['state']})
  allroutes=await c.fetch('SELECT observer,prefix::text prefix FROM inet_catalog.routes WHERE generation=$1 AND origin_asn=13335 ORDER BY observer,prefix',g)
  expectedkeys={(r['observer'],r['prefix']) for r in allroutes};keys=[];cursor=None;first=None
  while True:
   params={'asn':13335,'role':'origin','generation_id':g,'limit':200}
   if cursor:params['cursor']=cursor
   page=await get('/routes',params);first=first or page
   keys.extend((r['observer'],r['prefix']) for r in page['routes']);cursor=page['next_cursor']
   if not cursor:break
  assert len(keys)>200 and len(keys)==len(set(keys)) and set(keys)==expectedkeys
  report['checks'].append(f'all AS13335 origin pages reconcile: {len(keys)}')
  await get('/routes',{'asn':13335,'role':'transit','cursor':first['next_cursor']},422)
  await get('/routes',{'cursor':'invalid'},422)
  await get('/routes',{'generation_id':9223372036854775807},409)
  await get('/asns',{'q':'not-an-asn'},422)
  absent=await get('/asns/4294967295',{'generation_id':g});assert not absent['observed']
  prefix=await get('/routes',{'ip':'1.1.1.1','generation_id':g});assert prefix['routes'] and all(x['prefix']=='1.1.1.0/24' for x in prefix['routes'])
  v6=await get('/routes',{'family':'6','generation_id':g,'limit':20});assert all(':' in x['prefix'] for x in v6['routes'])
  globalpage=await get('/routes',{'generation_id':g});assert globalpage['total']==report['summary']['routes']
  report['checks'].extend(['changed-scope cursor rejected','invalid cursor rejected','expired generation409','invalid ASN422','absent ASN explicit','IP longest-prefix','IPv6 correct','full global count'])
  durations=[x['seconds'] for x in report['timings']];report['p95_seconds']=statistics.quantiles(durations,n=20)[18];report['max_seconds']=max(durations)
  report['elapsed_seconds']=round(time.monotonic()-started,2)
  Path('/tmp/asn-catalogue-qualification.json').write_text(json.dumps(report,indent=2,default=str))
  print(json.dumps({k:v for k,v in report.items() if k!='timings'},default=str),flush=True)
 await c.close()
asyncio.run(main())
