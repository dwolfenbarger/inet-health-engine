"""Read selected, maintained unicast route views without claiming global health."""
import json
from datetime import datetime,timezone
from ipaddress import ip_address,ip_network
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from api.deps import get_pg_pool

router=APIRouter(prefix='/routing',tags=['routing-state'])
GEO={'rrc23':(1.35,103.82),'route-views.kixp':(-1.29,36.82)}
RESOLVED=['repaired','snapshot_superseded']

def quality(row,pending,now):
    if row['baseline_state']!='reconstructed':return 'snapshot_required' if row['baseline_state']=='required' else 'awaiting_replay'
    if pending:return 'gap'
    if row['error_code']:return 'recovery_pending'
    if row['mode']=='mrt':
        return 'archive' if row['event_at'] and (now-row['event_at']).total_seconds()<=1800 else 'archive_stale'
    if row['peer_state']=='down':return 'peer_down'
    if row['stream_state']!='connected':return 'disconnected'
    if not row['received_at'] or (now-row['received_at']).total_seconds()>180:return 'receiver_stale'
    if not row['event_at'] or not -120<=(now-row['event_at']).total_seconds()<=180:return 'provider_delayed'
    return 'continuous'

async def read_observers(pool):
    rows=await pool.fetch('''SELECT o.*, (SELECT count(*) FROM routing_gaps g WHERE g.observer=o.id
        AND g.state<>ALL($1::text[])) AS pending_gaps FROM routing_observers o ORDER BY id''',RESOLVED)
    now=datetime.now(timezone.utc);result=[]
    for row in rows:
        meta=json.loads(row['metadata']) if isinstance(row['metadata'],str) else row['metadata']
        result.append({'id':row['id'],'source':row['source'],'collector':row['collector'],
          'peer_ip':str(row['peer_ip']),'peer_asn':row['peer_asn'],'mode':row['mode'],
          'city':meta.get('city'),'region':meta.get('region'),
          'lat':GEO.get(row['collector'],(0,0))[0],'lon':GEO.get(row['collector'],(0,0))[1],
          'epoch':row['epoch'],'generation':str(row['generation']),
          'continuity':quality(row,row['pending_gaps'],now),'stream_state':row['stream_state'],'peer_state':row['peer_state'],
          'baseline_state':row['baseline_state'],'snapshot_at':row['baseline_at'],
          'snapshot_v4':row['snapshot_v4'],'snapshot_v6':row['snapshot_v6'],
          'received_at':row['received_at'],'committed_at':row['committed_at'],'event_at':row['event_at'],
          'messages':row['messages'],'updates':row['updates'],'pending_gaps':row['pending_gaps'],
          'error_code':row['error_code']})
    return result

@router.get('/observers')
async def observers():
    try:
        pool=await get_pg_pool()
        return {'as_of':datetime.now(timezone.utc),'health_score':None,'observers':await read_observers(pool),
                'scope':'Selected observer-specific unicast tables; continuity is data coverage, not Internet health.'}
    except Exception:
        raise HTTPException(503,'Maintained routing evidence unavailable') from None

@router.get('/routes')
async def routes(observer:str|None=Query(None,max_length=100),
                 source:Literal['all','ris-continuous','routeviews']='all',
                 collector:str=Query('all',max_length=64),
                 prefix:str|None=Query(None,max_length=49),ip:str|None=Query(None,max_length=45),
                 asn:int|None=Query(None,ge=1,le=4294967295),
                 family:Literal['all','4','6']='all',include_withdrawn:bool=False,
                 limit:int=Query(25,ge=1,le=200)):
    try:
        if prefix:prefix=str(ip_network(prefix,strict=False))
        if ip:ip=str(ip_address(ip))
    except ValueError:raise HTTPException(422,'Enter a valid CIDR prefix or IP address') from None
    if ip and (prefix or include_withdrawn):raise HTTPException(422,'IP longest-prefix lookup selects present routes; use exact-prefix evidence for withdrawals')
    try:
        pool=await get_pg_pool()
        # DISTINCT ON chooses the longest currently present match per observer,
        # rather than a withdrawn more-specific tombstone or an arbitrary origin.
        select='SELECT DISTINCT ON (r.observer)' if ip else 'SELECT'
        order='r.observer,masklen(r.prefix) DESC,r.event_at DESC' if ip else 'r.event_at DESC,r.observer,r.prefix'
        sql=select+''' r.*,r.prefix::text AS prefix_text,o.source,o.collector,host(o.peer_ip) AS peer_ip,o.peer_asn,
            e.payload AS rpki_validation
          FROM routing_routes r JOIN routing_observers o ON o.id=r.observer
          LEFT JOIN source_evidence e ON e.source='rpki-local'
            AND e.evidence_id=r.prefix::text||'|'||r.origin_asn::text
            AND e.observed_at>now()-interval '20 minutes'
          WHERE r.epoch=o.epoch AND r.generation=o.generation
            AND ($1::text IS NULL OR o.id=$1) AND ($2='all' OR o.source=$2)
            AND ($3::text IS NULL OR r.prefix=$3::text::cidr)
            AND ($4::text IS NULL OR r.prefix>>=$4::text::inet)
            AND ($5::bigint IS NULL OR r.origin_asn=$5)
            AND ($6='all' OR family(r.prefix)::text=$6)
            AND ($7 OR r.present) AND ($9='all' OR o.collector=$9) ORDER BY '''+order+' LIMIT $8'
        rows=await pool.fetch(sql,observer,source,prefix,ip,asn,family,include_withdrawn,limit,collector)
        evidence=[]
        for row in rows:
            path=json.loads(row['path']) if isinstance(row['path'],str) else row['path']
            attrs=json.loads(row['attrs']) if isinstance(row['attrs'],str) else row['attrs']
            proof=row['rpki_validation']
            if isinstance(proof,str):proof=json.loads(proof)
            evidence.append({'observer':row['observer'],'source':row['source'],'collector':row['collector'],
                'peer_ip':row['peer_ip'],'peer_asn':row['peer_asn'],'prefix':row['prefix_text'],
                'time':row['event_at'],'origin_asn':row['origin_asn'],
                'as_path':path if isinstance(path,list) and all(isinstance(p,int) for p in path) else [],
                'path_segments':path,'present':row['present'],
                'basis':{0:'RIB snapshot',1:'MRT update',2:'RIS Live'}[row['rank']],
                'change_type':'snapshot' if row['rank']==0 else 'announce' if row['present'] else 'withdraw',
                'attrs':attrs,'evidence_id':row['evidence_id'],'epoch':row['epoch'],
                'rpki_status':proof['status'] if proof else 'unknown','rpki_validation':proof})
        view=await read_observers(pool)
        view=[v for v in view if (observer is None or v['id']==observer) and (source=='all' or v['source']==source) and (collector=='all' or v['collector']==collector)]
        return {'as_of':datetime.now(timezone.utc),'health_score':None,
            'scope':{'observer':observer,'source':source,'prefix':prefix,'ip':ip,'asn':asn,'family':family,
                     'include_withdrawn':include_withdrawn,'limit':limit,'collector':collector},'routes':evidence,'observers':view,
            'limitations':['Observer continuity describes reconstruction, not reachability or global Internet health.',
              'Snapshot time differs from the route last-change time retained in raw attributes.',
              'MRT publication introduces delay. An unrepaired gap leaves the view uncertain.',
              'No matching route in these selected views is not an Internet-wide withdrawal or all-clear.',
              'ADD_PATH is not reconstructed; archive audit refuses selected ADD_PATH data.']}
    except Exception:
        raise HTTPException(503,'Maintained routing evidence unavailable') from None

@router.get('/gaps')
async def gaps(observer:str|None=Query(None,max_length=100),limit:int=Query(25,ge=1,le=100)):
    try:
        pool=await get_pg_pool()
        rows=await pool.fetch('''SELECT id::text,observer,opened_at,from_at,until_at,reason,state,repaired_at,error_code
            FROM routing_gaps WHERE ($1::text IS NULL OR observer=$1) ORDER BY opened_at DESC LIMIT $2''',observer,limit)
        return {'as_of':datetime.now(timezone.utc),'gaps':[dict(r) for r in rows],'health_score':None}
    except Exception:raise HTTPException(503,'Routing continuity evidence unavailable') from None
