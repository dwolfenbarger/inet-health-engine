"""Verify an actual page cursor survives publication of the next generation."""
import json,sys
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode
root='http://127.0.0.1/api/v1/noc/catalogue/routes?'
p=Path('/tmp/asn-cursor-before-refresh.json')
def get(params):
 with urlopen(root+urlencode(params),timeout=20) as r:return json.load(r)
def keys(r):return [(x['observer'],x['prefix'],x['origin_asn'],x['path_segments']) for x in r['routes']]
if sys.argv[1]=='before':
 r=get({'asn':13335,'role':'origin','limit':100});assert r['next_cursor']
 p.write_text(json.dumps({'gen':r['generation']['id'],'cursor':r['next_cursor'],'keys':keys(r),'total':r['total']}))
 print('PINNED',r['generation']['id'],r['total'])
elif sys.argv[1]=='after':
 old=json.loads(p.read_text());latest=get({'limit':1});assert latest['generation']['id']>old['gen'],'New generation not yet published'
 first=get({'asn':13335,'role':'origin','limit':100,'generation_id':old['gen']})
 second=get({'asn':13335,'role':'origin','limit':100,'cursor':old['cursor']})
 assert json.loads(json.dumps(keys(first)))==old['keys']
 assert second['generation']['id']==old['gen'] and second['total']==old['total']
 assert not {(x[0],x[1]) for x in keys(first)}&{(x[0],x[1]) for x in keys(second)}
 print('REFRESH_CURSOR_PASS',old['gen'],'new',latest['generation']['id'])
else:raise SystemExit('before or after required')
