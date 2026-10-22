"""Read-only live release checks through the same nginx route as operators."""
import json, sys, time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError

base=sys.argv[1].rstrip('/')
def get(params):
    start=time.monotonic()
    with urlopen(base+'/api/v1/noc/snapshot?'+urlencode(params),timeout=20) as response:
        data=json.load(response)
    print('HTTP_CHECK',json.dumps({'params':params,'seconds':round(time.monotonic()-start,3),'summary':data['summary'],'source_state':data['source_state']}),flush=True)
    assert data['health_score'] is None
    assert len(data['routes']) <= 200
    return data

data=get({'minutes':5})
assert data['source_state']['state']=='fresh', 'RIS observations are not fresh'
assert data['summary']['updates']>0, 'No real updates observed'
assert all(r['collector'].startswith('rrc') for r in data['routes'])
assert all('stub' not in r['collector'] for r in data['routes'])
for family in ['4','6']:
    scoped=get({'minutes':15,'family':family})
    assert all((':' in r['prefix']) == (family=='6') for r in scoped['routes'])
route=next(r for r in data['routes'] if r['origin_asn'])
scoped=get({'minutes':15,'asn':route['origin_asn']})
assert scoped['routes'] and all(r['origin_asn']==route['origin_asn'] for r in scoped['routes'])
scoped=get({'minutes':15,'prefix':route['prefix']})
assert scoped['routes'] and all(r['prefix']==route['prefix'] for r in scoped['routes'])
scoped=get({'minutes':15,'collector':route['collector']})
assert scoped['routes'] and all(r['collector']==route['collector'] for r in scoped['routes'])
assert all(r['source']=='ris/'+route['collector'] for r in scoped['signals'])
for params in [{'minutes':61},{'prefix':"'; SELECT 1;--"}]:
    try:
        get(params)
        raise AssertionError('Invalid query accepted')
    except HTTPError as exc:
        assert exc.code==422
print('VERIFIED_REAL_DATA',json.dumps({'last_observation':data['last_observation'],'as_of':data['as_of'],'summary':data['summary'],'example_prefix':route['prefix'],'example_asn':route['origin_asn'],'source':'RIPE RIS Live'}),flush=True)
Path('/tmp/inet-live-verification.json').write_text(json.dumps({'base':base,'snapshot':data,'verified_at':time.time()},indent=2))
