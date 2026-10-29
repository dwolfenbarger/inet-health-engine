"""Bounded live acceptance using retained observations and existing public GETs."""
import datetime,json,os,subprocess,sys,time,uuid
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
from ipaddress import ip_address,ip_network
BASE=os.environ.get('INET_PATHS_QA_URL','http://127.0.0.1:8013');checks=[]
def get(path,params=None,code=200):
 t=time.monotonic()
 try:
  with urlopen(BASE+path+('?' +urlencode(params) if params else ''),timeout=23) as r:status=r.status;body=json.loads(r.read())
 except HTTPError as e:status=e.code;body=json.loads(e.read())
 checks.append({'path':path,'params':params,'code':status,'seconds':time.monotonic()-t});assert status==code,(status,path,body);return body
p=subprocess.run([sys.executable,str(Path(__file__).with_name('qualify-path-investigations.py'))],env=os.environ.copy(),capture_output=True,text=True,timeout=240)
assert p.returncode==0,(p.stdout[-1000:],p.stderr[-2000:]);old=json.loads(p.stdout);old['browser_visual_acceptance']='Recorded separately in project browser evidence'
q={'source_asn':2497,'destination_asn':13335,'minutes':20160,'limit':2}
h=get('/api/v1/noc/paths/history',q);steps=0
while not h['items'] and h['next_cursor'] and steps<6:
 h=get('/api/v1/noc/paths/history',q|{'cursor':h['next_cursor']});steps+=1
assert h['items'],'No retained source/destination observation available for the live case'
anchor=h['items'][0];cq={'event_id':anchor['event_id'],'asn':13335,'limit':2}
case=get('/api/v1/noc/incidents/case',cq);assert case['anchor']['event_id']==anchor['event_id'] and case['as_of']<case['expires_at']
assert case['current_coverage']['policy']['retention_hours']==336
assert case['page_impact']['scope'].startswith('This timeline page only') and case['anchor']['raw_evidence_url'].endswith(anchor['event_id'])
if case['timeline']['next_cursor']:
 second=get('/api/v1/noc/incidents/case',cq|{'case_id':case['case_id'],'cursor':case['timeline']['next_cursor']})
 assert (second['case_id'],second['as_of'],second['expires_at'],second['anchor'],second['validation'],second['sampled'])==(case['case_id'],case['as_of'],case['expires_at'],case['anchor'],case['validation'],case['sampled'])
 assert not {e['event_id'] for e in case['timeline']['items']} & {e['event_id'] for e in second['timeline']['items']}
get('/api/v1/noc/incidents/case',cq|{'case_id':case['case_id'],'asn':4294967294},422)
get('/api/v1/noc/incidents/case',cq|{'cursor':str(uuid.uuid4())},422)
for item in case['validation']['items']:
 v=item['validation']
 if v:
  assert v['status'] in ['valid','invalid','not-found'] and v['prefix']==case['anchor']['prefix'] and v['origin_asn']==item['origin_asn']
  delta=(datetime.datetime.fromisoformat(v['checked_at'])-datetime.datetime.fromisoformat(v['generated_at'])).total_seconds();assert -120<=delta<=3600
window=case['timeline'].get('effective_window') or case['timeline']['requested_window']
for d in case['measurements']['items']:
 assert datetime.datetime.fromisoformat(window['from'])<=datetime.datetime.fromisoformat(d['observed_at'])<=datetime.datetime.fromisoformat(window['through'])
 if case['scope']['resource']:
  af=d['pair']['family'];target=ip_address(d['target'][f'ip_v{af}']);resource=case['scope']['resource']
  assert target in ip_network(resource,strict=False) if '/' in resource else target==ip_address(resource)
v=get('/api/v1/noc/visibility',{'asn':13335,'resource':'1.1.1.1'})
assert v['global_health'] is None and v['global_coverage_percent'] is None and any(c['state']=='observed' for c in v['routing_cells'])
assert all(c['family']==4 for c in v['routing_cells'])
six=get('/api/v1/noc/visibility',{'resource':'2606:4700:4700::1111','family':'6'});assert all(c['family']==6 for c in six['routing_cells']) and any(c['state']=='observed' for c in six['routing_cells'])
missing=get('/api/v1/noc/visibility',{'asn':4294967294});assert all(c['state']=='not_observed_in_view' for c in missing['routing_cells'])
get('/api/v1/noc/visibility',{'resource':'1.1.1.1','family':'6'},422);get('/api/v1/noc/visibility',{'collector':'invented'},422)
global_view=get('/api/v1/noc/visibility');assert len(global_view['regions'])>=7 and len(global_view['routing_cells'])>=4
print(json.dumps({'pass':True,'path_acceptance':old,'requests':checks,
 'case':{'event_id':anchor['event_id'],'kind':case['anchor']['kind'],'candidate':case['anchor']['candidate'],'prefix':case['anchor']['prefix'],
  'as_of':case['as_of'],'expires_at':case['expires_at'],'timeline_rows':len(case['timeline']['items']),'has_next':bool(case['timeline']['next_cursor']),
  'validation_state':case['validation']['state'],'validation_statuses':[i['validation']['status'] if i['validation'] else 'unavailable' for i in case['validation']['items']],
  'correlated_measurements':len(case['measurements']['items']),'measurement_state':case['measurements']['state']},
 'coverage':{'cells':len(global_view['routing_cells']),'regions':len(global_view['regions']),'configured_matching_directions':len(global_view['performance']['directions']),
  'source_state':global_view['performance'].get('source_state'),'health_score':None},
 'limits':['Short bounded API acceptance, not representative public-load capacity or global-health validation.','RPKI and public measurements have independent clocks and scopes.']},indent=2))
