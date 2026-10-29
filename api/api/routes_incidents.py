"""Frozen, evidence-led incident cases from existing retained observations."""
import asyncio,json,uuid
from datetime import datetime,timedelta,timezone
from typing import Literal
import httpx
from fastapi import APIRouter,HTTPException,Query
from api.deps import get_pg_pool,get_redis
from api import routes_diagnostics as diagnostics
from api.routes_paths import validate_collector
from api.routes_coverage import mesh_data,decode
from api.incident_evidence import compact_event,scoped_event,page_impact,measurement_match
from api.local_validation import validate
import os

router=APIRouter(prefix='/incidents',tags=['incident-investigations'])
UTC=timezone.utc
_slots=asyncio.Semaphore(2)
LIMITS=[
 'A case is an investigation anchored to an exact retained observation, not a confirmed incident or cause.',
 'Routing samples and selected-peer diagnostics share RIS lineage. Repeated reports and viewpoints do not establish independent confirmations.',
 'Current RPKI validity has its own clock; it is not proof of authorization at the historical event time or intent.',
 'Nearby endpoint performance is correlated only by resource and time. Congestion, hijacking, global impact and recovery remain unconfirmed.',
 'Source gaps remain unknown even after a separate RIB repair. Case and timeline pages have an absolute five-minute expiry.',
]


def encode(v):return json.dumps(v,default=lambda x:x.isoformat() if isinstance(x,datetime) else str(x))


async def validation_context(anchor):
    prefix=anchor.get('prefix')
    if not prefix:return {'state':'not_prefix_scoped','items':[]}
    values=list(dict.fromkeys(a for a in [anchor.get('expected_asn'),anchor.get('origin_asn')] if type(a) is int and a>0))[:2]
    if not values:return {'state':'origin_unknown','items':[]}
    try:
        async with httpx.AsyncClient(base_url=os.environ.get('ROUTINATOR_URL','http://127.0.0.1:8323'),timeout=2,
          limits=httpx.Limits(max_connections=2)) as client:
            answers=await asyncio.gather(*(validate(client,prefix,a) for a in values))
        return {'state':'current_snapshot_only','checked_at':datetime.now(UTC),'items':[
          {'origin_asn':a,'validation':answer,'state':'available' if answer else 'unavailable'} for a,answer in zip(values,answers)]}
    except Exception:return {'state':'validation_unavailable','items':[]}


async def initial_case(pool,event_id,asn,resource,family,collector,limit):
    row=await pool.fetchrow('''SELECT e.id,e.event_id::text,e.observer,e.sequence,e.epoch,e.event_at,e.received_at,
      e.prefix::text prefix,e.kind,e.candidate,e.origin_asn,e.expected_asn,e.involved_asns,e.evidence,o.collector,o.peer_asn
      FROM routing_diagnostic_events e JOIN routing_observers o ON o.id=e.observer WHERE e.event_id=$1''',event_id)
    if not row:raise HTTPException(404,'This anchor observation is no longer retained')
    anchor=dict(row);anchor['evidence']=decode(anchor['evidence']);parsed=diagnostics.resource_value(resource)
    if family!='all' and anchor['prefix']:
        from ipaddress import ip_network
        if ip_network(anchor['prefix']).version!=int(family):raise HTTPException(422,'Anchor is outside this address family')
    if asn and asn not in anchor['involved_asns']:raise HTTPException(422,'Anchor is outside the selected ASN involvement')
    if collector!='all' and anchor['collector']!=collector:raise HTTPException(422,'Anchor is outside this collector filter')
    if parsed:
        prefixes=[anchor['prefix']] if anchor['prefix'] else anchor['evidence'].get('facts',{}).get('distinct_prefixes',[])
        from api.routes_issues import prefix_matches
        if not any(prefix_matches(p,parsed) for p in prefixes):raise HTTPException(422,'Anchor is outside the selected resource')
    scope_resource=str(parsed) if parsed else anchor['prefix']
    scope_asn=asn if asn else None if scope_resource else anchor.get('expected_asn') or anchor.get('origin_asn') or anchor['peer_asn']
    now=datetime.now(UTC);until=min(now,anchor['event_at']+timedelta(minutes=15))
    timeline=await diagnostics.history(asn=scope_asn,resource=scope_resource,minutes=30,family=family,collector=collector,cursor=None,limit=limit,until=until)
    if timeline.get('outside_retention'):raise HTTPException(409,'Anchor context is outside the current retention window')
    timeline=json.loads(encode(timeline));timeline['expires_at']=(datetime.fromisoformat(timeline['as_of'])+timedelta(seconds=diagnostics.PAGE_TTL)).isoformat()
    anchor=compact_event(anchor)
    window=timeline.get('effective_window') or timeline['requested_window'];lower=datetime.fromisoformat(window['from']);through=datetime.fromisoformat(window['through'])
    sampled=[];sampled_state='not_prefix_scoped'
    if anchor['prefix']:
        try:
            rows=await pool.fetch('''SELECT time,event_id::text,event_type,affected_prefix,origin_asn,expected_asn,source,
              raw_data->'as_path' path,raw_data->>'peer_asn' peer_asn FROM bgp_anomalies
              WHERE affected_prefix=$1 AND time>$2 AND time<=$3 AND source LIKE 'ris/%'
              ORDER BY time DESC,event_id DESC LIMIT 51''',anchor['prefix'],lower,through,timeout=2)
            sampled=[dict(r) for r in rows[:50]];sampled_state='bounded_context' if len(rows)<=50 else 'capped_context'
        except Exception:sampled_state='unavailable'
    measured=[];measurement_state='no_matching_recorded_direction'
    try:
        mesh=await mesh_data(pool,family)
        measured=[m for d in mesh['directions'] if (m:=measurement_match(d,scope_resource,scope_asn,lower,through))]
        measurement_state='correlated_recordings' if measured else measurement_state
    except Exception:measurement_state='collection_unavailable'
    validation=await validation_context(anchor)
    return {'anchor':anchor,'timeline':timeline,'sampled':{'state':sampled_state,'items':sampled,
      'scope':'Frozen latest50 sampled anomaly reports for the exact anchor prefix and context window. Shared RIS lineage; omission/absence does not establish no incident.'},
      'measurements':{'state':measurement_state,'items':measured},'validation':validation,
      'scope':{'asn':scope_asn,'resource':scope_resource,'family':family,'collector':collector,'minutes':30,'until':until.isoformat()},
      'as_of':timeline['as_of'],'expires_at':timeline['expires_at'],
      'retention_hours':await pool.fetchval('SELECT retention_hours FROM routing_diagnostic_policy WHERE id'),'limits':LIMITS}


@router.get('/case')
async def case(event_id:uuid.UUID,asn:int|None=Query(None,ge=1,le=4294967295),resource:str|None=Query(None,max_length=49),
               family:Literal['all','4','6']='all',collector:str=Query('all',max_length=64),case_id:uuid.UUID|None=None,cursor:uuid.UUID|None=None,
               limit:int=Query(25,ge=1,le=50)):
    validate_collector(collector);parsed=diagnostics.resource_value(resource)
    request_scope={'event_id':str(event_id),'asn':asn,'resource':str(parsed) if parsed else None,'family':family,'collector':collector,'limit':limit}
    try:
        cache=await get_redis();pool=await get_pg_pool()
        if case_id:
            saved=await cache.get('noc:incident-case:'+str(case_id))
            if not saved:raise HTTPException(409,'Case expired; refresh the investigation')
            result=json.loads(saved)
            if result['request_scope']!=request_scope:raise HTTPException(422,'Case belongs to different filters')
            if datetime.now(UTC)>=datetime.fromisoformat(result['expires_at']):raise HTTPException(409,'Case expired; refresh')
            if await pool.fetchval('SELECT retention_hours FROM routing_diagnostic_policy WHERE id')<result['retention_hours']:
                raise HTTPException(409,'Retention changed; refresh the case')
            if cursor:
                p=result['scope'];timeline=await diagnostics.history(asn=p['asn'],resource=p['resource'],minutes=30,family=p['family'],collector=p['collector'],cursor=cursor,limit=limit,until=datetime.fromisoformat(p['until']))
                timeline=json.loads(encode(timeline));timeline['expires_at']=result['expires_at']
                if timeline['as_of']!=result['as_of']:raise HTTPException(422,'Timeline cursor belongs to a different case capture')
                result['timeline']=timeline
        else:
            if cursor:raise HTTPException(422,'A timeline cursor requires its case identity')
            try:await asyncio.wait_for(_slots.acquire(),timeout=.05)
            except TimeoutError:raise HTTPException(429,'Case investigation is busy; retry shortly') from None
            try:result=await initial_case(pool,event_id,asn,resource,family,collector,limit)
            finally:_slots.release()
            result['request_scope']=request_scope;case_id=uuid.uuid4();serialized=encode(result)
            if len(serialized.encode())>2*1024**2:raise HTTPException(503,'Case exceeds the bounded display budget; focus a prefix or smaller page')
            expires=datetime.fromisoformat(result['expires_at']);ttl=int((expires-datetime.now(UTC)).total_seconds())
            if ttl<=0:raise HTTPException(409,'Case expired while reading evidence; refresh')
            await cache.setex('noc:incident-case:'+str(case_id),ttl,serialized)
        p=result['scope'];items=[scoped_event(e,p['resource'],p['asn'],p['family']) for e in result['timeline']['items']]
        result['timeline']['items']=items
        result['page_impact']=page_impact(items);result['case_id']=str(case_id)
        result['current_coverage']=await diagnostics.read_status(pool,collector)
        return result
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Incident evidence unavailable; no health conclusion is available') from None
