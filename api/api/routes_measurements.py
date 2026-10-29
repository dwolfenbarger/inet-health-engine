"""Read existing public Atlas probe measurements. This module never schedules probes."""
import asyncio
import hashlib
import json
import math
from datetime import datetime,timedelta,timezone
from ipaddress import ip_address
from typing import Literal

import httpx
from fastapi import APIRouter,HTTPException,Query
from api.deps import get_pg_pool,get_redis
from api.path_evidence import trace_observation,ping_observation,compare_traces,valid_asn
from api.routes_routing import quality

router=APIRouter(prefix='/paths',tags=['measured-path-investigations'])
BASE='https://atlas.ripe.net/api/v2/'
UTC=timezone.utc
_provider_slots=asyncio.Semaphore(2)
_mapping_slots=asyncio.Semaphore(4)
LIMITS=[
 'These are existing public measurements from the selected probe to one target; no new probes are scheduled.',
 'Probe ASN, country and measurement target ASN are current provider metadata, not proof of their historical identity or ASN-wide performance.',
 'Traceroute TTLs show responding interfaces and missing replies. A timeout does not establish forwarding loss or identify congestion.',
 'Per-hop RTT includes the return path. Adjacent-hop RTT subtraction and geographic ownership are not inferred.',
 'Hop ASNs are current routing-origin hints from selected maintained views, not router ownership or authoritative historical mapping.',
 'Routing and Atlas evidence have different vantage points and clocks. Correlation does not establish causation or global impact.',
 'Public provider history is fetched on demand; local raw-measurement retention is not extended by this endpoint.',
]


def key(parts):return 'noc:measurements:v1:'+hashlib.sha256(json.dumps(parts,sort_keys=True).encode()).hexdigest()


async def cached(parts,loader,ttl=120):
    cache=await get_redis();name=key(parts);saved=await cache.get(name)
    if saved:return json.loads(saved)
    try:await asyncio.wait_for(_provider_slots.acquire(),timeout=.05)
    except TimeoutError:raise HTTPException(429,'Public measurement requests are busy; retry shortly') from None
    try:
        # Recheck after admission so completed requests can share their cached evidence.
        saved=await cache.get(name)
        if saved:return json.loads(saved)
        value=await asyncio.wait_for(loader(),timeout=18)
        encoded=json.dumps(value,default=str)
        if len(encoded.encode())<=1024**2:await cache.setex(name,ttl,encoded)
        return value
    finally:_provider_slots.release()


async def provider_json(client,path,params=None,max_bytes=2*1024**2):
    # Only fixed provider paths assembled from validated integer IDs are permitted.
    async with client.stream('GET',BASE+path,params=params) as response:
        response.raise_for_status();raw=bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw)>max_bytes:raise ValueError('Provider response exceeds input budget')
    return json.loads(raw,parse_constant=lambda _:None)


def checked_probe(probe,source_asn,family,probe_id=None):
    if (type(probe.get('id')) is not int or (probe_id is not None and probe['id']!=probe_id) or
            probe.get('is_public') is not True or probe.get(f'asn_v{family}')!=source_asn):
        raise HTTPException(409,'Probe identity no longer matches the selected source ASN and family; refresh discovery')
    return {'id':probe['id'],'asn':source_asn,'family':family,'country':probe.get('country_code'),
            'status':probe.get('status'),'is_anchor':probe.get('is_anchor',False),
            'metadata_checked_at':datetime.now(UTC).isoformat(),
            'asn_basis':'Current public probe metadata; historical source ASN is not independently verified.'}


@router.get('/probes')
async def probes(source_asn:int=Query(...,ge=1,le=4294967295),family:Literal['4','6']='4',page:int=Query(1,ge=1,le=100)):
    async def load():
        async with httpx.AsyncClient(timeout=6,follow_redirects=False) as client:
            body=await provider_json(client,'probes/',{'asn_v'+family:source_asn,'is_public':'true','status':1,'page_size':10,'page':page,'sort':'id'})
        if not isinstance(body,dict) or not isinstance(body.get('results'),list) or len(body['results'])>10:raise ValueError('Invalid probe inventory')
        values=[checked_probe(p,source_asn,int(family)) for p in body['results']]
        return {'as_of':datetime.now(UTC).isoformat(),'source_asn':source_asn,'family':int(family),'probes':values,
                'page':page,'next_page':page+1 if body.get('next') and page<100 else None,'capped':bool(body.get('next') and page==100),
                'scope':'Current connected public Atlas probes in this ASN/family. Live discovery pages can change; no results do not imply an ASN outage.'}
    try:return await cached(['probes',source_asn,family,page],load,300)
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Public probe discovery unavailable; no health conclusion can be made') from None


@router.get('/measurements')
async def measurements(source_asn:int=Query(...,ge=1,le=4294967295),destination_asn:int=Query(...,ge=1,le=4294967295),
                       probe_id:int=Query(...,ge=1,le=2147483647),family:Literal['4','6']='4',
                       kind:Literal['traceroute','ping']='traceroute',page:int=Query(1,ge=1,le=100)):
    async def load():
        async with httpx.AsyncClient(timeout=6,follow_redirects=False) as client:
            probe=checked_probe(await provider_json(client,f'probes/{probe_id}/'),source_asn,int(family),probe_id)
            body=await provider_json(client,'measurements/',{'current_probes':probe_id,'target_asn':destination_asn,'af':int(family),
                'type':kind,'is_public':'true','status':2,'page_size':10,'page':page,'sort':'id'})
        if not isinstance(body,dict) or not isinstance(body.get('results'),list) or len(body['results'])>10:raise ValueError('Invalid measurement inventory')
        items=[]
        for meta in body['results']:
            if meta.get('is_public') is not True or meta.get('type')!=kind or meta.get('af')!=int(family) or meta.get('target_asn')!=destination_asn:
                raise ValueError('Measurement discovery identity mismatch')
            target=str(ip_address(meta['target_ip']))
            if not ip_address(target).is_global or ip_address(target).version!=int(family):continue
            items.append({'id':meta['id'],'target':target,'target_asn':meta['target_asn'],'kind':kind,
                          'interval_seconds':meta.get('interval'),'description':str(meta.get('description',''))[:160]})
        return {'as_of':datetime.now(UTC).isoformat(),'probe':probe,'measurements':items,'page':page,
                'next_page':page+1 if body.get('next') and page<100 else None,'capped':bool(body.get('next') and page==100),
                'scope':'Existing ongoing public measurements currently including this probe, to targets attributed to the destination ASN. This is a bounded live inventory, not a complete reachability test.'}
    try:return await cached(['measurements',source_asn,destination_asn,probe_id,family,kind,page],load,300)
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Public measurement discovery unavailable; no health conclusion can be made') from None


async def origin_hint(pool,address):
    parsed=ip_address(address)
    if not parsed.is_global:
        return {'state':'non_global_address','origin_asns':[],'views':[],'scope':'Private/shared/reserved address; ownership is unknown.'}
    cache=await get_redis();name=key(['origin_hint',address]);saved=await cache.get(name)
    if saved:return json.loads(saved)
    async with _mapping_slots:
        try:
            rows=await pool.fetch('''SELECT * FROM (
              SELECT DISTINCT ON (r.observer) r.observer,r.origin_asn,r.prefix::text prefix,r.event_at,
               o.baseline_state,o.error_code,o.mode,o.peer_state,o.stream_state,o.received_at,o.event_at observer_event_at,
               EXISTS(SELECT 1 FROM routing_gaps g WHERE g.observer=o.id AND g.state NOT IN ('repaired','snapshot_superseded')) pending
              FROM routing_routes r JOIN routing_observers o ON o.id=r.observer
              WHERE r.present AND r.epoch=o.epoch AND r.generation=o.generation AND r.prefix>>=$1::text::inet
              ORDER BY r.observer,masklen(r.prefix) DESC,r.prefix
              ) lpm ORDER BY observer LIMIT 17''',address,timeout=2)
            now=datetime.now(UTC);views=[]
            for row in rows[:16]:
                views.append({'observer':row['observer'],'prefix':row['prefix'],'origin_asn':row['origin_asn'],
                              'route_event_at':row['event_at'].isoformat(),'continuity':quality({**dict(row),'event_at':row['observer_event_at']},row['pending'],now)})
            answer={'state':'observed_routing_origin' if views else 'unobserved','checked_at':now.isoformat(),
                    'origin_asns':sorted({r['origin_asn'] for r in rows if valid_asn(r['origin_asn'])}),
                    'views':views,'view_cap_reached':len(rows)>16,
                    'scope':'Current LPM per selected maintained viewpoint. Origin ASN is a routing hint, not router ownership or historical mapping.'}
        except Exception:
            answer={'state':'mapping_unavailable','origin_asns':[],'views':[],'checked_at':datetime.now(UTC).isoformat()}
    await cache.setex(name,300,json.dumps(answer));return answer


@router.get('/measurement')
async def measurement(source_asn:int=Query(...,ge=1,le=4294967295),destination_asn:int=Query(...,ge=1,le=4294967295),
                      probe_id:int=Query(...,ge=1,le=2147483647),measurement_id:int=Query(...,ge=1,le=2147483647),
                      family:Literal['4','6']='4',kind:Literal['traceroute','ping']='traceroute',until:datetime|None=None):
    now=datetime.now(UTC)
    if until is not None:
        if until.tzinfo is None:raise HTTPException(422,'Window end requires UTC or an explicit offset')
        until=until.astimezone(UTC)
        if until>now or until<now-timedelta(days=14):raise HTTPException(422,'Measurement window end must be within the past fourteen days')
    through=until or now.replace(second=0,microsecond=0);lower=through-timedelta(hours=1)
    async def load():
        async with httpx.AsyncClient(timeout=6,follow_redirects=False) as client:
            probe_raw,meta=await asyncio.gather(provider_json(client,f'probes/{probe_id}/'),provider_json(client,f'measurements/{measurement_id}/'))
            probe=checked_probe(probe_raw,source_asn,int(family),probe_id)
            if (meta.get('id')!=measurement_id or meta.get('is_public') is not True or meta.get('type')!=kind or
                    meta.get('af')!=int(family) or meta.get('target_asn')!=destination_asn):
                raise HTTPException(409,'Measurement identity no longer matches the selected destination ASN, type and family')
            target=ip_address(meta['target_ip'])
            if not target.is_global or target.version!=int(family):raise HTTPException(422,'Measurement target is not a public address in this family')
            params={'probe_ids':probe_id,'start':math.ceil(lower.timestamp()),'stop':int(through.timestamp())}
            raw=await provider_json(client,f'measurements/{measurement_id}/results/',params)
        if not isinstance(raw,list) or len(raw)>120:raise ValueError('Measurement result budget exceeded')
        # Validate identities even in older rows; a wrong-probe response must not be silently used.
        for row in raw:
            if row.get('msm_id')!=measurement_id or row.get('prb_id')!=probe_id or row.get('type')!=kind or row.get('af')!=int(family):
                raise ValueError('Provider result identity mismatch')
        raw.sort(key=lambda x:x['timestamp'],reverse=True);chosen=[];seen=set()
        for row in raw:
            if row['timestamp'] in seen:continue
            seen.add(row['timestamp']);chosen.append(row)
            if len(chosen)==2:break
        parser=trace_observation if kind=='traceroute' else ping_observation
        observations=[parser(r,meta,probe,lower,through) for r in chosen]
        mapping_total=0;mapping_selected=0
        if kind=='traceroute' and observations:
            addresses=list(dict.fromkeys(r['address'] for obs in observations for h in obs['hops'] for r in h['responders']))
            mapping_total=len(addresses);selected=addresses[:32];mapping_selected=len(selected);pool=await get_pg_pool()
            answers=await asyncio.gather(*(origin_hint(pool,address) for address in selected));hints=dict(zip(selected,answers))
            for obs in observations:
                for hop in obs['hops']:
                    for responder in hop['responders']:
                        responder['mapping']=hints.get(responder['address'],{'state':'mapping_budget_exceeded','origin_asns':[],'views':[]})
        if kind=='traceroute':comparison=compare_traces(observations[0],observations[1] if len(observations)>1 else None) if observations else {'state':'no_observations'}
        else:
            comparison={'state':'no_prior_observation'}
            if len(observations)>1:
                a,b=observations[:2];x=a['average_rtt_ms'];y=b['average_rtt_ms']
                comparison={'state':'two_observations','average_rtt_delta_ms':x-y if x is not None and y is not None else None,
                            'scope':'Difference between two returned endpoint samples; not an established performance baseline or confirmed congestion.'}
        observed=datetime.fromisoformat(observations[0]['observed_at']) if observations else None
        state='no_observations' if not observations else 'historical_observation' if until else 'observation_available'
        if observations and observations[0].get('measurement_error'):state='measurement_error'
        if until is None and observed and (through-observed).total_seconds()>1200:state='stale'
        return {'as_of':datetime.now(UTC).isoformat(),'state':state,'kind':kind,'probe':probe,
                'source_asn':source_asn,'destination_asn':destination_asn,'target':str(target),
                'requested_window':{'from':lower.isoformat(),'through':through.isoformat()},'observations':observations,
                'comparison':comparison,'mapping_budget':32,'addresses_selected_for_mapping':mapping_selected,'observed_addresses':mapping_total,
                'measurement_url':f'https://atlas.ripe.net/measurements/{measurement_id}/',
                'raw_evidence_url':str(httpx.URL(BASE+f'measurements/{measurement_id}/results/',params=params)),
                'limitations':LIMITS}
    try:return await cached(['result',source_asn,destination_asn,probe_id,measurement_id,family,kind,through.isoformat()],load,60)
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Measured path evidence unavailable or invalid; no healthy result is inferred') from None
