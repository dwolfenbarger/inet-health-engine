import urllib.request,urllib.error,json,time
base='http://127.0.0.1:8000/api/v1/noc'
def get(path,code=200):
 try:
  with urllib.request.urlopen(base+path,timeout=30) as r:
   assert r.status==code;return json.load(r)
 except urllib.error.HTTPError as e:
  assert e.code==code,(path,e.code,code)
def main():
 data=get('/sources');assert data['health_score'] is None and len(data['sources'])>=7
 print('SOURCE_STATES',[(s['id'],s['state'],s['records']) for s in data['sources']],flush=True)
 for path in ['/snapshot','/snapshot?family=6','/snapshot?asn=4288000245','/snapshot?prefix=2001:db8::/32']:
  d=get(path);assert d['health_score'] is None
  for route in d['routes']:
   proof=route.get('rpki_validation')
   if proof:assert route['rpki_status']==proof['status'] and proof['generated_at']
   else:assert route['rpki_status']=='unknown'
  print('HTTP_PASS',path,'routes',len(d['routes']),flush=True)
 for source in ['atlas','ioda','rpki-local']:
  d=get('/evidence/'+source+'?limit=10');assert d['health_score'] is None
  if source=='atlas':
   assert d['records']
   assert all(r['asn'] is None for r in d['records'])
   assert all(r['payload']['rtt_ms'] is None or r['payload']['rtt_ms']>=0 for r in d['records'])
  print('EVIDENCE_PASS',source,'sample',len(d['records']),flush=True)
 assert get('/evidence/atlas?asn=4288000245')['records']==[]
 get('/evidence/unknown',404);get('/snapshot?asn=4294967296',422);get('/evidence/atlas?limit=100000',422)
 print('SOURCE_HTTP_CHECKS_PASSED',flush=True)
main()
