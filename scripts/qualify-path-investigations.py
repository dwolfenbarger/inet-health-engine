"""Bounded live API acceptance; only public GETs, no active measurement creation."""
import datetime,json,os,sys,time
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
BASE=os.environ.get('INET_PATHS_QA_URL','http://127.0.0.1:8013')
checks=[]
def get(path,params=None,expected=200):
 url=BASE+path+('?' +urlencode(params) if params else '');at=time.monotonic()
 try:
  with urlopen(url,timeout=23) as r:code=r.status;value=json.loads(r.read())
 except HTTPError as e:code=e.code;value=json.loads(e.read())
 checks.append({'path':path,'params':params,'code':code,'seconds':time.monotonic()-at})
 assert code==expected,(path,code,value);return value
q={'source_asn':2497,'destination_asn':13335,'limit':2}
routes=get('/api/v1/noc/paths/routes',q);assert routes['items'] and any(i['segment']['ordered'] for i in routes['items'])
assert routes['generation']['backend']=='incremental'
if routes['next_cursor']:
 page=get('/api/v1/noc/paths/routes',q|{'cursor':routes['next_cursor']});assert page['generation']['id']==routes['generation']['id']
 assert not {(x['observer'],x['prefix']) for x in page['items']} & {(x['observer'],x['prefix']) for x in routes['items']}
 get('/api/v1/noc/paths/routes',q|{'cursor':routes['next_cursor'],'source_asn':2914},422)
for source,resource,family in [(2497,'1.1.1.1','4'),(15547,'2606:4700:4700::1111','6'),(2497,'1.1.1.0/24','4')]:
 value=get('/api/v1/noc/paths/routes',q|{'source_asn':source,'resource':resource,'family':family,'limit':25});assert value['items']
get('/api/v1/noc/paths/routes',q|{'collector':'invented'},422)
get('/api/v1/noc/paths/routes',q|{'resource':'1.1.1.1','family':'6'},422)
hist=get('/api/v1/noc/paths/history',q|{'minutes':20160});assert hist['coverage']['policy']['retention_hours']==336
if hist['next_cursor']:
 page=get('/api/v1/noc/paths/history',q|{'minutes':20160,'cursor':hist['next_cursor']});assert page['as_of']==hist['as_of'] and page['expires_at']==hist['expires_at']
 get('/api/v1/noc/paths/history',q|{'minutes':1440,'cursor':hist['next_cursor']},422)
outside=get('/api/v1/noc/paths/history',q|{'until':(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=15)).isoformat()});assert outside['outside_retention'] and not outside['items']
mesh=get('/api/v1/noc/mesh');eligible=[d for d in mesh['directions'] if d['pair'].get('trace_measurement_id') and d.get('target_asn')]
assert eligible,'No configured existing public trace has verified destination ASN metadata'
errors=[];proof=None
for direction in eligible[:3]:
 pair=direction['pair'];af=pair['family'];traceq={'source_asn':direction['source'][f'as_v{af}'],'destination_asn':direction['target_asn'],'probe_id':direction['source']['probe'],'family':str(af),'measurement_id':pair['trace_measurement_id']}
 try:
  measured=get('/api/v1/noc/paths/measurement',traceq)
  assert measured['observations'] and measured['observations'][0]['hops']
  assert any(h['responders'] for h in measured['observations'][0]['hops'])
  probe=get('/api/v1/noc/paths/probes',{'source_asn':traceq['source_asn'],'family':str(af)})
  inventory=get('/api/v1/noc/paths/measurements',{k:v for k,v in traceq.items() if k!='measurement_id'})
  ping=get('/api/v1/noc/paths/measurement',traceq|{'measurement_id':pair['measurement_id'],'kind':'ping'})
  assert ping['observations']
  proof={'direction':pair['id'],'probe_id':traceq['probe_id'],'trace_measurement_id':traceq['measurement_id'],
    'source_asn':traceq['source_asn'],'destination_asn':traceq['destination_asn'],'family':af,
    'trace_time':measured['observations'][0]['observed_at'],'trace_hops':len(measured['observations'][0]['hops']),
    'unknown_hops':sum(not h['responders'] for h in measured['observations'][0]['hops']),
    'mapping_states':sorted({r['mapping']['state'] for h in measured['observations'][0]['hops'] for r in h['responders']}),
    'probe_options':len(probe['probes']),'measurement_options':len(inventory['measurements']),
    'ping_time':ping['observations'][0]['observed_at'],'ping_average_ms':ping['observations'][0]['average_rtt_ms']}
  break
 except Exception as e:errors.append({'direction':pair['id'],'error':str(e)[:400]})
assert proof,errors
catalogue=get('/api/v1/noc/catalogue/asns',{'limit':1});assert catalogue['generation']['backend']=='incremental'
print(json.dumps({'pass':True,'requests':checks,'measured_proof':proof,'other_direction_failures':errors,
 'route_snapshot':routes['generation']['captured_at'],'route_prefixes':[x['prefix'] for x in routes['items']],
 'history_items':len(hist['items']),'browser_visual_acceptance':'Unavailable; project browser permission denied'},indent=2))
