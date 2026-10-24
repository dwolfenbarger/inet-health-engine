"""Read-only issue workflow qualification. Run against candidate or native API."""
import json,os,time
from urllib.request import urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
base=os.environ.get('INET_ISSUES_QA_URL','http://127.0.0.1:8011/api/v1/noc')
report={'base':base,'checks':[],'timings':[],'examples':{}}
def get(path='',params=None,expected=200):
 t=time.monotonic()
 try:r=urlopen(base+path+'?'+urlencode(params or {}),timeout=25)
 except HTTPError as e:r=e
 with r:code,body=r.status,r.read()
 report['timings'].append({'path':path,'params':params,'seconds':round(time.monotonic()-t,3)})
 assert code==expected,(path,params,code,body[:200])
 return json.loads(body)
q={'minutes':1440,'limit':100};page=get('/issues',q);first=page;seen=[]
while True:
 seen+=page['items']
 if page['next_offset'] is None:break
 page=get('/issues',q|{'snapshot_id':first['snapshot_id'],'offset':page['next_offset']})
assert len(seen)==len({x['id'] for x in seen})==first['total']==sum(first['summary'].values())
report['checks'].append('Every snapshot page reconciles without duplicate or hidden groups')
report['summary']=first['summary']
for kind in ['bgp_flap','bgp_hijack','withdrawal_surge']:
 issue=next((x for x in seen if x['kind']==kind),None)
 assert issue,kind+' requires a real retained candidate'
 detail=get('/issues/detail',{'event_id':issue['id'],'observed_at':issue['last_seen']})
 assert detail['candidate']['event_id']==issue['id']
 report['examples'][kind]={'id':issue['id'],'prefix':issue['affected_prefix'],'origin':issue['origin_asn'],'expected':issue['expected_asn'],'time':issue['last_seen'],'raw':detail['candidate']['raw_data'],'updates':len(detail['updates'])}
 if kind=='bgp_flap':assert detail['candidate']['raw_data']['transitions']>0
 if kind=='bgp_hijack':
  victim=get('/issues',q|{'asn':issue['expected_asn'],'kind':kind})
  assert any(x['affected_prefix']==issue['affected_prefix'] and x['expected_asn']==issue['expected_asn'] for x in victim['items'])
  path=detail['candidate']['raw_data'].get('as_path',[])
  if path:
   transit=get('/issues',q|{'asn':path[0],'kind':kind})
   assert transit['total']>0
  report['checks'].append('Expected/victim and recorded path ASN involvement searchable')
 if issue['affected_prefix']:
  prefix=get('/issues',q|{'resource':issue['affected_prefix']})
  assert prefix['total']>0 and not prefix['summary']['performance']
 report['checks'].append(kind+' exact observation, evidence and prefix drilldown')
mesh=next((x for x in seen if x['kind']=='performance'),None)
if mesh:
 detail=get('/mesh/'+mesh['measurement']['pair']['id'])
 assert detail['evidence']['pair']['id']==mesh['measurement']['pair']['id']
 report['examples']['performance']={'id':mesh['id'],'time':mesh['last_seen'],'state':mesh['state']}
 report['checks'].append('Measured path change exposes genuine named-pair evidence')
else:report['checks'].append('No current measured candidate; do not infer an all-clear')
get('/issues',q|{'snapshot_id':first['snapshot_id'],'asn':4294967295},422)
get('/issues',{'snapshot_id':'00000000-0000-4000-8000-000000000000'},409)
get('/issues',{'resource':'invalid'},422)
empty=get('/issues',{'asn':4294967295});assert empty['total']==0 and empty['limits']
v6=get('/issues',q|{'family':'6'});assert all(':' in x['affected_prefix'] for x in v6['items'] if x['evidence_type']=='routing')
report['checks']+=['Changed scope422, expired409, invalid422, unknown ASN explicit, IPv6 scope']
report['max_seconds']=max(x['seconds'] for x in report['timings'])
print(json.dumps(report,indent=2))
