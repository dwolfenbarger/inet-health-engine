import json,time,urllib.request,urllib.error
base='http://127.0.0.1:8000/api/v1/noc'
for path in ['/sources','/routing/observers','/routing/routes?ip=1.1.1.1','/routing/routes?ip=2606:4700:4700::1111','/routing/routes?limit=200','/routing/gaps']:
 start=time.monotonic()
 try:
  with urllib.request.urlopen(base+path,timeout=25) as r:data=json.load(r)
  assert data['health_score'] is None
  print('HTTP',path,'seconds',round(time.monotonic()-start,3),'records',len(data.get('routes',[])),flush=True)
  if 'observers' in data:print('CONTINUITY',[(o['id'],o['continuity'],str(o['event_at'])) for o in data['observers']],flush=True)
  for route in data.get('routes',[])[:2]:print('ROUTE',route['prefix'],route['origin_asn'],route['peer_asn'],route['basis'],flush=True)
 except Exception as exc:print('HTTP_FAILED',path,type(exc).__name__,str(exc),flush=True);raise
print('MAINTAINED_HTTP_CHECKS_PASSED')
