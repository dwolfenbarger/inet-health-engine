"""Read-only live API qualification; writes no observations."""
import json,os,time
from urllib.request import urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
base=os.environ.get('INET_DIAGNOSTICS_QA_URL','http://127.0.0.1:8011/api/v1/noc')
report={'checks':[],'timings':[]}
def get(path,params=None,expected=200):
 t=time.monotonic()
 try:r=urlopen(base+path+'?'+urlencode(params or {}),timeout=25)
 except HTTPError as e:r=e
 with r:code,body=r.status,r.read()
 report['timings'].append({'path':path,'seconds':round(time.monotonic()-t,3)})
 assert code==expected,(path,code,body[:180]);return json.loads(body)
s=get('/diagnostics/status');assert s['policy']['enabled'] and s['policy']['state']=='running' and len(s['observers'])>0
assert all(o['consumer_state'] in ['waiting','processing','catching_up'] and not o['error_code'] for o in s['observers'])
report['coverage']=s
q={'asn':s['observers'][0]['peer_asn'],'minutes':1440,'limit':10}
a=get('/diagnostics/history',q);assert a['items'],'Live history required'
seen={r['event_id'] for r in a['items']};p=a
for i in range(3):
 if not p['next_cursor']:break
 p=get('/diagnostics/history',q|{'cursor':p['next_cursor']})
 assert not seen.intersection(r['event_id'] for r in p['items'])
 seen.update(r['event_id'] for r in p['items'])
report['checks'].append('Live ASN history pages contain no duplicate observations')
if a['next_cursor']:
 get('/diagnostics/history',q|{'cursor':a['next_cursor'],'asn':4294967295},422)
 get('/diagnostics/history',q|{'cursor':'00000000-0000-4000-8000-000000000000'},409)
row=next(x for x in a['items'] if x['prefix'])
for resource in [row['prefix'],row['prefix'].split('/')[0]]:
 scoped=get('/diagnostics/history',{'resource':resource,'minutes':1440})
 assert scoped['items'];assert any(x['prefix']==row['prefix'] for x in scoped['items'])
d=get('/diagnostics/detail',{'event_id':row['event_id']});assert d['candidate']['event_id']==row['event_id'] and d['detection_mode']=='continuous'
assert d['candidate']['raw_data']['after']==row['evidence'].get('after',{})
get('/diagnostics/history',{'resource':'bad'},422);get('/diagnostics/history',{},422)
empty=get('/diagnostics/history',{'asn':4294967295});assert not empty['items'] and empty['limits']
get('/diagnostics/detail',{'event_id':'00000000-0000-4000-8000-000000000000'},404)
issues=get('/issues',{'minutes':1440});assert issues['continuous_status'] and not issues['continuous_error']
report['checks']+=['Prefix/IP drilldown, exact detail, missing/invalid scope, unknown ASN and expired page behavior','Issue list includes continuous coverage without hiding sampled/measurement evidence']
report['examples']={'history':row,'detail':d,'summary':issues['summary']}
report['max_seconds']=max(x['seconds'] for x in report['timings'])
print(json.dumps(report,indent=2))
