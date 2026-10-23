"""Real API/database checks; no schema or test-data writes."""
import asyncio,os,sys,json
from pathlib import Path
root=Path('/home/david/inet-health-engine')
for line in (root/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
candidate=Path(sys.argv[1]) if len(sys.argv)>1 else root
sys.path[:0]=[str(candidate/'collectors'),str(candidate/'api')]
async def main():
 from fastapi import FastAPI
 from api.routes_coverage import router
 import httpx
 app=FastAPI();app.include_router(router)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
  r=await client.get('/coverage');assert r.status_code==200,r.text;body=r.json()
  assert body['health_score'] is None and body['global_coverage_percent'] is None
  assert len(body['regions'])==7
  print('COVERAGE',json.dumps(body['regions']),flush=True)
  r=await client.get('/mesh');assert r.status_code==200,r.text;data=r.json()
  assert data['health_score'] is None and len(data['directions'])==28,data
  available=[d for d in data['directions'] if d['collection_state'] in ['observations_available','baseline_building','measurement_change_candidate']]
  print('DIRECTIONS',len(data['directions']),'AVAILABLE',len(available),'SOURCE_STATUS',data['source_status'],flush=True)
  assert len(available)>=20
  for d in data['directions']:
   if d.get('current'):
    stats=d['current'];assert stats['received']<=stats['sent']
    if stats['received']==0:assert stats['median_of_sample_medians_ms'] is None
   assert 'raw_stats' not in d
  pair=available[0];r=await client.get('/mesh/'+pair['pair']['id']);assert r.status_code==200,r.text
  assert r.json()['evidence']['raw_stats']['probe_id']==pair['source']['probe']
  assert len((await client.get('/mesh?family=6')).json()['directions'])==14
  filtered=(await client.get('/mesh?asn=4294967295')).json();assert filtered['directions']==[]
  assert (await client.get('/mesh?family=9')).status_code==422
  assert (await client.get('/mesh?asn=4294967296')).status_code==422
  assert (await client.get('/mesh/no-such-direction')).status_code==404
 print('COVERAGE_HTTP_AND_DB_PASSED',flush=True)
asyncio.run(main())
