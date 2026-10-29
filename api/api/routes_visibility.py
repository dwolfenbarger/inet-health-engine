"""Actionable resource coverage from existing captured views and named measurements."""
import asyncio,hashlib,json
from datetime import datetime,timezone
from ipaddress import ip_address
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from api.deps import get_pg_pool,get_redis
from api.catalogue_backend import for_generation,ScopedPool
from api.routes_catalogue import generation,ensure_retained
from api.routes_paths import resource_value,validate_collector,public_generation
from api.routes_routing import read_observers
from api.routes_diagnostics import read_status
from api.routes_coverage import mesh_data,REGIONS
from api.path_queries import snapshot_lpm_query
from api.incident_evidence import coverage_action,measurement_match

router=APIRouter(prefix='/visibility',tags=['actionable-coverage'])
UTC=timezone.utc
_slots=asyncio.Semaphore(2)


async def build(pool,asn,resource,family,collector,requested_generation):
    parsed=resource_value(resource,family);g=await generation(pool,requested_generation);scoped=for_generation(pool,g)
    views=[o for o in await read_observers(pool) if collector=='all' or o['collector']==collector]
    captured={o['id'] for o in g['observers']};views=[o for o in views if o['id'] in captured]
    if len(views)>32:raise HTTPException(503,'Configured viewpoint budget exceeded')
    rows=[]
    if parsed is not None:
        if not hasattr(parsed,'prefixlen'):
            sql,args=snapshot_lpm_query(scoped,g,str(parsed),collector,limit=33);rows=await pool.fetch(sql,*args,timeout=4)
        else:
            clauses=['r.generation=$1','r.prefix=$2::text::cidr'];args=[g['id'],str(parsed)]
            if collector!='all':args.append(collector);clauses.append(f'r.collector=${len(args)}')
            if isinstance(scoped,ScopedPool):rows=await scoped.page_fetch('routes',clauses,args,33)
            else:rows=await pool.fetch('SELECT r.*,r.prefix::text prefix_text FROM inet_catalog.routes r WHERE '+' AND '.join(clauses)+' ORDER BY observer,prefix LIMIT 33',*args)
    cells=[];facets=g['summary'].get('facets',[])
    for view in views:
        for af in ([parsed.version] if parsed else [int(family)] if family!='all' else [4,6]):
            selected=[r for r in rows if r['observer']==view['id'] and r['family']==af]
            sample=selected[0] if selected else None
            if asn and parsed is None:
                # Use the existing ASN membership key. An ordered LIMIT over
                # route arrays can scan a whole viewpoint for an absent ASN.
                clauses=['r.generation=$1','r.asn=$2','r.observer=$3','family(r.prefix)=$4'];args=[g['id'],asn,view['id'],af]
                if isinstance(scoped,ScopedPool):found=await scoped.member_routes(clauses,args,1)
                else:found=await pool.fetch('''SELECT r.*,r.prefix::text prefix_text FROM inet_catalog.routes r
                    WHERE r.generation=$1 AND r.members @> ARRAY[$2::bigint] AND r.observer=$3 AND r.family=$4 LIMIT 1''',*args)
                sample=found[0] if found else None
            count=sum(f['routes'] for f in facets if f['observer']==view['id'] and int(f['family'])==af) if facets or not g['summary']['routes'] else None
            matches=bool(sample and (not asn or asn in sample['members']))
            state=('observed' if matches else 'resource_asn_disagreement' if sample else 'not_observed_in_view') if asn or parsed else ('count_unavailable' if count is None else 'captured_entries' if count else 'no_captured_entries')
            cells.append({'observer':view['id'],'collector':view['collector'],'peer_asn':view['peer_asn'],'family':af,
              'collector_region':view['region'],'city':view['city'],'continuity':view['continuity'],'event_at':view['event_at'],
              'pending_gaps':view['pending_gaps'],'state':state,'sample_prefix':sample['prefix_text'] if sample else None,
              'sample_origin_asn':sample['origin_asn'] if sample else None,'asn_matches':matches if asn else None,
              'captured_route_entries':count if not asn and parsed is None else None,
              'next_action':coverage_action(view['continuity'],view['mode']) if sample or not (asn or parsed) else 'Compare another viewpoint/family or inspect retained history. Absence from this view is not a withdrawal or outage.'})
    performance_error=False
    try:mesh=await mesh_data(pool,family)
    except Exception:mesh={'directions':[],'source_status':{'state':'unavailable'}};performance_error=True
    directions=[]
    for d in mesh['directions']:
        af=d['pair']['family']
        if asn and asn not in [d['source'].get(f'as_v{af}'),d.get('target_asn')]:continue
        if parsed is not None:
            try:target=ip_address(d['target'][f'ip_v{af}'])
            except (ValueError,KeyError):continue
            if hasattr(parsed,'prefixlen'):
                if target.version!=parsed.version or target not in parsed:continue
            elif target!=parsed:continue
        directions.append(d)
    regions=[]
    for name in REGIONS:
        configured=[v for v in views if v['region']==name]
        endpoints={d[k]['probe'] for d in directions for k in ['source','target'] if d[k]['region']==name}
        regions.append({'region':name,'routing_viewpoints':len(configured),'measured_endpoints':len(endpoints),
          'state':'selected_endpoints_only' if configured or endpoints else 'no_configured_endpoint',
          'next_action':'Compare independent networks and source continuity.' if configured or endpoints else 'No configured endpoint here; regional network health is unknown.'})
    await ensure_retained(scoped,g['id'])
    return {'as_of':datetime.now(UTC),'scope':{'asn':asn,'resource':str(parsed) if parsed else None,'family':family,'collector':collector},
      'generation':public_generation(g),'routing_cells':cells,'regions':regions,
      'performance':{'state':'collection_unavailable' if performance_error else 'configured_recordings' if directions else 'no_configured_matching_measurement','directions':directions,
        'source_state':mesh.get('source_status',{}).get('state','unknown'),
        'next_action':'Check the measurement source; routing evidence remains available.' if performance_error else 'Inspect source times and the exact probe/target.' if directions else 'Use public probe/measurement discovery for this ASN pair. No matching configured measurement establishes no performance coverage, not a healthy result.',
        'scope':'Only configured public mesh directions, filtered by actual target IP or recorded endpoint ASN. Discovery of other public probes is separate; routing-collector filters do not filter measurement probes.'},
      'diagnostics':await read_status(pool,collector),'global_health':None,'global_coverage_percent':None,
      'limits':['Routing cells refer to one captured catalogue generation; source continuity is current and has a separate clock.',
        'CIDR means exact prefix; an IP uses longest present prefix per viewpoint before ASN matching. ASN-only cells prove presence with one example, not a route count.',
        'Collector/anchor region locates the observation endpoint, not the geography of all routes or affected users.',
        'No configured observation or measurement does not establish absence of an incident, recovery, or universal absence of public measurements.',
        'A selected-peer diagnostic retention target does not imply complete fourteen-day input or local retention of all Atlas raw measurements.']}


@router.get('')
async def visibility(asn:int|None=Query(None,ge=1,le=4294967295),resource:str|None=Query(None,max_length=49),
                     family:Literal['all','4','6']='all',collector:str=Query('all',max_length=64),
                     generation_id:int|None=Query(None,ge=1)):
    validate_collector(collector);parsed=resource_value(resource,family)
    key='noc:visibility:v1:'+hashlib.sha256(json.dumps([asn,str(parsed) if parsed else None,family,collector,generation_id]).encode()).hexdigest()
    try:
        cache=await get_redis();saved=await cache.get(key)
        if saved:
            result=json.loads(saved);pool=await get_pg_pool()
            await ensure_retained(for_generation(pool,result['generation']),result['generation']['id'])
            return result
        try:await asyncio.wait_for(_slots.acquire(),timeout=.05)
        except TimeoutError:raise HTTPException(429,'Coverage lookup is busy; retry shortly') from None
        try:result=await build(await get_pg_pool(),asn,resource,family,collector,generation_id)
        finally:_slots.release()
        await cache.setex(key,15,json.dumps(result,default=lambda x:x.isoformat() if isinstance(x,datetime) else str(x)))
        return result
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Resource coverage unavailable; health remains unknown') from None
