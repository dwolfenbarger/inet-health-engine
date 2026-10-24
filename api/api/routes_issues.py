"""Operational issue candidates from existing detectors and actual mesh measurements.

An observation group is not a confirmed incident. Routing input remains sampled
until the continuous detector phase is qualified. No RTT or path is synthesized.
"""
import json
import uuid
from datetime import datetime, timezone, timedelta
from ipaddress import ip_network, ip_address
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from api.deps import get_pg_pool, get_redis
from api.routes_coverage import mesh_data, decode

router = APIRouter(prefix='/issues', tags=['issues'])
KINDS = {'bgp_flap': 'Route flapping', 'bgp_hijack': 'Suspected origin hijack',
         'withdrawal_surge': 'Withdrawal surge', 'performance': 'Measured path change'}
LIMITATIONS = [
    'Routing candidates come from the bounded sampled RIS detector, not the continuous maintained RIB.',
    'Repeated detector reports are grouped; report counts are not flap-transition counts or independent confirmations.',
    'Last observation is not proof that an issue remains active or has recovered.',
    'Origin changes can be legitimate. Detector confidence is not a verified probability of hijacking.',
    'Measured-path candidates apply only to their named probe and destination; they do not establish ASN-wide performance.',
]


def prefix_matches(value, requested):
    try:
        network = ip_network(value, strict=False)
        return (network.version == requested.version and network.subnet_of(requested)) if hasattr(requested, 'prefixlen') else requested in network
    except (ValueError, TypeError):
        return False


def normalize_group(r):
    item = dict(r)
    item['id'] = str(item.pop('latest_id'))
    item['kind'] = item.pop('event_type')
    item['title'] = KINDS.get(item['kind'], item['kind'])
    item['evidence_type'] = 'routing'
    item['state'] = 'candidate'
    item['confirmation'] = 'Unconfirmed; recovery not established'
    for k in ['first_seen', 'last_seen']:
        if hasattr(item[k], 'isoformat'): item[k] = item[k].isoformat()
    return item


async def build_snapshot(pool, minutes, asn, resource, family, collector):
    now = datetime.now(timezone.utc)
    clauses = ["time > $1", "time <= $2", "source LIKE 'ris/%'", "event_type = ANY($3::text[])"]
    args = [now-timedelta(minutes=minutes), now, list(KINDS)[:3]]
    if asn:
        args.append(asn); a = len(args)
        # Include the expected origin and observed transit/peer involvement where recorded.
        clauses.append(f"(origin_asn=${a} OR expected_asn=${a} OR raw_data->'as_path' @> jsonb_build_array(${a}::bigint) OR raw_data->>'peer_asn'=${a}::text)")
    if collector != 'all':
        args.append('ris/'+collector); clauses.append(f'source=${len(args)}')
    if family != 'all':
        args.append(family); clauses.append(f"(CASE WHEN position(':' in affected_prefix)>0 THEN '6' ELSE '4' END)=${len(args)}")
    # Text prefixes are validated in Python rather than casting legacy malformed rows.
    rows = await pool.fetch('''SELECT event_type, affected_prefix, origin_asn, expected_asn,
        min(time) first_seen, max(time) last_seen, count(*) reports,
        max(severity) severity, max(confidence) detector_confidence,
        count(DISTINCT (source,raw_data->>'peer_asn')) viewpoints,
        array_agg(DISTINCT source) sources,
        (array_agg(event_id::text ORDER BY time DESC,event_id DESC))[1] latest_id
        FROM bgp_anomalies WHERE '''+' AND '.join(clauses)+'''
        GROUP BY event_type,affected_prefix,origin_asn,expected_asn
        ORDER BY max(time) DESC,event_type,affected_prefix,origin_asn,expected_asn''', *args)
    routing = [normalize_group(r) for r in rows if not resource or prefix_matches(r['affected_prefix'],resource)]
    latest = await pool.fetchval("SELECT time FROM bgp_updates WHERE time>now()-interval '24 hours' AND collector=ANY($1::text[]) ORDER BY time DESC LIMIT 1",['rrc00','rrc11','rrc14','rrc16','rrc21','rrc15','rrc23'])
    input_age = (now-latest).total_seconds() if latest else None
    performance = []; performance_error = None
    try:
        mesh = await mesh_data(pool,family,asn)
        if resource is None and collector == 'all':
            for d in mesh['directions']:
                if not d.get('candidates') or not d.get('observed_at'): continue
                observed = datetime.fromisoformat(d['observed_at'])
                if not now-timedelta(minutes=minutes) < observed <= now+timedelta(seconds=120): continue
                af=d['pair']['family']
                performance.append({'id':'mesh:'+d['pair']['id'],'kind':'performance','title':'Measured path change',
                    'evidence_type':'measurement','state':d['collection_state'],
                    'confirmation':'Measurement threshold exceeded; cause and current impact unconfirmed',
                    'affected_prefix':None,'origin_asn':d['source'].get(f'as_v{af}'),'expected_asn':d.get('target_asn'),
                    'first_seen':None,'last_seen':d['observed_at'],'reports':None,'viewpoints':1,'sources':['atlas-mesh'],
                    'severity':None,'detector_confidence':None,'measurement':d})
    except Exception:
        performance_error='Measured-path evidence unavailable; routing results remain available.'
    items=sorted(routing+performance,key=lambda x:(x['last_seen'],x['id']),reverse=True)
    return {'as_of':now.isoformat(),'items':items,'summary':{k:sum(x['kind']==k for x in items) for k in KINDS},
        'routing_input':{'last_observation':latest.isoformat() if latest else None,
            'state':'unavailable' if input_age is None else 'fresh' if input_age<=240 else 'stale'},
        'performance_error':performance_error,'performance_scope':'Prefix/IP and routing-collector filters exclude mesh candidates; endpoint mapping to those scopes is not established.',
        'limits':LIMITATIONS,'scope':{'minutes':minutes,'asn':asn,'resource':str(resource) if resource else None,'family':family,'collector':collector}}


@router.get('')
async def issues(minutes:int=Query(60,ge=5,le=1440),asn:int|None=Query(None,ge=1,le=4294967295),
    resource:str|None=Query(None,max_length=49),family:Literal['all','4','6']='all',
    collector:str=Query('all',max_length=64),kind:Literal['all','bgp_flap','bgp_hijack','withdrawal_surge','performance']='all',
    snapshot_id:uuid.UUID|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100)):
    requested=None
    if resource:
        try:requested=ip_network(resource,strict=False) if '/' in resource else ip_address(resource)
        except ValueError:raise HTTPException(422,'Enter an IPv4/IPv6 prefix or IP address') from None
    from api.routes_noc import COLLECTORS
    if collector!='all' and collector not in COLLECTORS:raise HTTPException(422,'Unknown collector')
    scope={'minutes':minutes,'asn':asn,'resource':str(requested) if requested else None,'family':family,'collector':collector}
    try:
        cache=await get_redis()
        if snapshot_id:
            saved=await cache.get('noc:issues:'+str(snapshot_id))
            if not saved:raise HTTPException(409,'Issue list expired. Refresh issues to get a new list.')
            result=json.loads(saved)
            if result['scope']!=scope:raise HTTPException(422,'Issue snapshot belongs to different filters')
        else:
            result=await build_snapshot(await get_pg_pool(),minutes,asn,requested,family,collector)
            snapshot_id=uuid.uuid4()
            await cache.setex('noc:issues:'+str(snapshot_id),300,json.dumps(result,default=str))
        selected=[x for x in result['items'] if kind=='all' or x['kind']==kind]
        return {k:v for k,v in result.items() if k!='items'}|{'snapshot_id':str(snapshot_id),'total':len(selected),
            'items':selected[offset:offset+limit],'next_offset':offset+limit if offset+limit<len(selected) else None,
            'offset':offset,'limit':limit,'kind':kind}
    except HTTPException:raise
    except Exception:
        raise HTTPException(503,'Issue evidence unavailable. No health conclusion can be made.') from None


@router.get('/detail')
async def detail(event_id:uuid.UUID,observed_at:datetime):
    if observed_at.tzinfo is None:raise HTTPException(422,'Evidence time requires a timezone')
    try:
        pool=await get_pg_pool()
        row=await pool.fetchrow('''SELECT time,event_id::text,event_type,affected_prefix,origin_asn,expected_asn,
            severity,confidence,source,raw_data FROM bgp_anomalies
            WHERE time=$1 AND event_id=$2 AND source LIKE 'ris/%' ''',observed_at,event_id)
        if not row:raise HTTPException(404,'This candidate observation is not available')
        item=dict(row);item['raw_data']=decode(item['raw_data']) or {}
        raw=item['raw_data'];prefix=item['affected_prefix'];updates=[]
        if prefix:
            updates=await pool.fetch('''SELECT time,prefix,origin_asn,as_path,change_type,collector,peer_asn,rpki_status
                FROM bgp_updates WHERE prefix=$1 AND time BETWEEN $2 AND $3
                AND collector=$4 AND ($5::bigint IS NULL OR peer_asn=$5)
                ORDER BY time DESC LIMIT 101''',prefix,observed_at-timedelta(minutes=15),observed_at+timedelta(minutes=5),
                item['source'].removeprefix('ris/'),raw.get('peer_asn') if type(raw.get('peer_asn')) is int else None)
        evidence=[dict(r) for r in updates[:100]]
        for u in evidence:
            u['change_type']=(u['change_type'] or 'unknown').split('.')[-1].lower()
            u['rpki_status']=(u['rpki_status'] or 'unknown').split('.')[-1].lower()
        before=next((u for u in evidence if u['time']<observed_at and u['change_type']=='announce' and u['origin_asn']==item['expected_asn']),None) if item['expected_asn'] else None
        validation=None
        if prefix and item['origin_asn']:
            proof=await pool.fetchrow("SELECT observed_at,payload FROM source_evidence WHERE source='rpki-local' AND evidence_id=$1 AND observed_at>now()-interval '60 minutes'",f"{prefix}|{item['origin_asn']}")
            if proof:validation={'observed_at':proof['observed_at'],'evidence':decode(proof['payload'])}
        return {'candidate':item,'updates':evidence,'updates_capped':len(updates)>100,'previous_origin_announcement':before,
            'current_origin_validation':validation,'scope':'Sampled updates from this collector/peer: 15 minutes before through 5 minutes after the observation; latest 100. Missing samples do not establish missing routes.',
            'limits':LIMITATIONS+['Current RPKI validation has a separate clock and does not prove authorization at the event time.','A prior origin is detector memory; a previous full path is available only when a matching sampled announcement is present.']}
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Candidate detail unavailable; retry shortly') from None
