"""Evidence coverage and bounded public-mesh summaries; no health score."""
import json
from datetime import datetime,timezone
from fastapi import APIRouter,Query,HTTPException
from typing import Literal
from api.deps import get_pg_pool
from api.routes_routing import read_observers
router=APIRouter()
REGIONS=['North America','South America','Europe','Africa','Asia','Oceania','Middle East']
def decode(value):return json.loads(value) if isinstance(value,str) else value

def mesh_state(payload,now,failed=False):
    at=payload.get('observed_at')
    if failed:return 'collection_error'
    if not at:return 'no_observations'
    if any(payload.get(k,{}).get('id')!=1 for k in ['source_probe_status','target_probe_status'] if k in payload):return 'probe_disconnected'
    age=(now-datetime.fromisoformat(at)).total_seconds()
    if not -120<=age<=1200:return 'stale'
    return payload['state']

async def mesh_data(pool,family='all',asn=None):
    now=datetime.now(timezone.utc)
    status=await pool.fetchrow("SELECT * FROM source_status WHERE source='atlas-mesh'")
    detail=decode(status['detail']) if status else {}
    errors={e['pair'] for e in detail.get('errors',[])}
    rows=await pool.fetch("SELECT evidence_id,observed_at,payload FROM source_evidence WHERE source='atlas-mesh' AND ($1='all' OR family::text=$1) AND evidence_id=ANY($2::text[]) ORDER BY evidence_id LIMIT 32",family,[p['id'] for p in detail.get('pairs',[])])
    directions=[]
    for row in rows:
        p=decode(row['payload']);af=p['pair']['family']
        if asn and asn not in [p['source'][f'as_v{af}'],p['target'][f'as_v{af}']]:continue
        state=mesh_state(p,now,row['evidence_id'] in errors or bool(status and status['state']=='error'))
        # Raw series is available from the bounded detail route, not every refresh.
        value={k:v for k,v in p.items() if k!='raw_stats'};value['collection_state']=state
        directions.append(value)
    known={d['pair']['id'] for d in directions}
    anchors={a['probe']:a for a in detail.get('anchors',[])}
    for pair in detail.get('pairs',[]):
        if pair['id'] in known or (family!='all' and str(pair['family'])!=family):continue
        source,target=anchors[pair['source_probe']],anchors[pair['target_probe']];af=pair['family']
        if asn and asn not in [source[f'as_v{af}'],target[f'as_v{af}']]:continue
        directions.append({'pair':pair,'source':source,'target':target,'collection_state':'no_observations','observed_at':None})
    return {'as_of':now,'health_score':None,'directions':directions,'source_status':{
        'state':status['state'] if status else 'unavailable','last_success':status['last_success'] if status else None,
        'error_code':status['error_code'] if status else None},
        'scope':'Selected anchor-to-anchor directions; ASN filter selects endpoints only. Independent of routing resource and time-window filters.',
        'limitations':['No global coverage denominator or health percentage is established.',
          'A measured path does not establish performance for every network in its endpoint countries.',
          'Candidate thresholds indicate a measurement change, not confirmed congestion or an outage.']}

@router.get('/mesh')
async def mesh(family:Literal['all','4','6']='all',asn:int|None=Query(None,ge=1,le=4294967295)):
    try:return await mesh_data(await get_pg_pool(),family,asn)
    except Exception:raise HTTPException(503,'Performance evidence unavailable') from None

@router.get('/mesh/{pair_id}')
async def raw_mesh(pair_id:str):
    if len(pair_id)>80:raise HTTPException(422,'Invalid direction identity')
    try:
        pool=await get_pg_pool();row=await pool.fetchrow("SELECT observed_at,payload FROM source_evidence WHERE source='atlas-mesh' AND evidence_id=$1",pair_id)
        if not row:raise HTTPException(404,'No evidence for this direction')
        p=decode(row['payload'])
        status=await pool.fetchrow("SELECT state,detail FROM source_status WHERE source='atlas-mesh'")
        detail=decode(status['detail']) if status else {}
        failed=bool(status and status['state']=='error') or pair_id in {e['pair'] for e in detail.get('errors',[])}
        return {'health_score':None,'observed_at':row['observed_at'],'evidence':p,'collection_state':mesh_state(p,datetime.now(timezone.utc),failed)}
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Performance evidence unavailable') from None

@router.get('/coverage')
async def coverage():
    try:
        pool=await get_pg_pool();views=await read_observers(pool);performance=await mesh_data(pool)
        regions=[]
        for region in REGIONS:
            observers=[v for v in views if v['region']==region]
            directions=[d for d in performance['directions'] if region in [d['source']['region'],d['target']['region']]]
            current=[d for d in directions if d['collection_state'] not in ['no_observations','stale','collection_error','insufficient_recent_samples','probe_disconnected']]
            anchors={d[k]['probe'] for d in directions for k in ['source','target'] if d[k]['region']==region}
            regions.append({'region':region,'routing_observers':len(observers),
              'snapshot_v4':sum(v['snapshot_v4'] for v in observers),'snapshot_v6':sum(v['snapshot_v6'] for v in observers),
              'reconstructed_observers':sum(v['baseline_state']=='reconstructed' for v in observers),
              'unrepaired_gaps':sum(v['pending_gaps'] for v in observers),
              'anchor_endpoints':len(anchors),'configured_directions':len(directions),'recent_directions':len(current)})
        return {'as_of':datetime.now(timezone.utc),'health_score':None,'global_coverage_percent':None,'regions':regions,
          'scope':'Configured collector locations and selected anchor endpoints; not country-wide or ASN-wide coverage.',
          'prefix_count_basis':'Sum of loaded baseline entries; prefixes can appear at several observers. Not a unique global prefix count.',
          'limitations':['Public prefix/ASN coverage denominator and periodic independent RIB reconciliation are not yet implemented.',
            'The geography of a route collector is not the geography of all routes it sees.',
            'Performance measurements and routing views have different observers and cannot be merged into one health percentage.']}
    except Exception:raise HTTPException(503,'Coverage evidence unavailable') from None
