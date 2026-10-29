"""Bounded source-to-origin investigations using individual observed route paths."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from api.deps import get_pg_pool, get_redis
from api.catalogue_backend import ScopedPool, for_generation
from api.routes_catalogue import generation, ensure_retained, fingerprint, encode_cursor, decode_cursor
from api.routes_routing import read_observers
from api.routes_diagnostics import PAGE_TTL, read_status, history_item
from api.path_evidence import as_segment, evidence_pairs

router = APIRouter(prefix='/paths', tags=['path-investigations'])
UTC = timezone.utc
LIMITS = [
    'Each segment belongs to one observed route. Separate observations are never stitched into a path.',
    'The destination ASN is the route origin. The source ASN must appear in its ordered AS path.',
    'This advertised control-plane segment is not the source router’s selected forwarding route, a traceroute or measured reachability.',
    'Collector cities do not locate transit networks. Missing or withdrawn evidence is not a global outage or all-clear.',
    'Current observer continuity is separate from the browsing capture and from historical source gaps.',
]


def resource_value(value, family):
    if not value:return None
    try:
        parsed = ip_network(value, strict=False) if '/' in value else ip_address(value)
    except ValueError:
        raise HTTPException(422, 'Enter an IPv4/IPv6 prefix or IP address') from None
    if family != 'all' and parsed.version != int(family):
        raise HTTPException(422, 'Destination resource and address family differ')
    return parsed


def validate_collector(collector):
    from api.routes_noc import COLLECTORS
    if collector != 'all' and collector not in COLLECTORS:
        raise HTTPException(422, 'Unknown configured collector')


def public_generation(g):
    return {k: v for k, v in g.items() if k != 'summary'}


@router.get('/routes')
async def routes(source_asn:int=Query(...,ge=1,le=4294967295),
                 destination_asn:int=Query(...,ge=1,le=4294967295),
                 resource:str|None=Query(None,max_length=49), family:Literal['all','4','6']='all',
                 collector:str=Query('all',max_length=64), cursor:str|None=Query(None,max_length=800),
                 generation_id:int|None=Query(None,ge=1), limit:int=Query(25,ge=1,le=50)):
    validate_collector(collector);parsed = resource_value(resource, family)
    normalized = str(parsed) if parsed is not None else None
    scope = {'source_asn':source_asn, 'destination_asn':destination_asn,
             'resource':normalized, 'family':family, 'collector':collector}
    mark = fingerprint(['path-v1', scope]);page = decode_cursor(cursor, mark) if cursor else None
    if page and generation_id is not None and generation_id != page[0]:
        raise HTTPException(422, 'Page generation differs')
    try:
        native = await get_pg_pool();g = await generation(native, page[0] if page else generation_id)
        pool = for_generation(native, g);args = [g['id']];clauses = ['r.generation=$1']
        def bind(condition, value):
            args.append(value);clauses.append(condition.replace('?', '$'+str(len(args))))
        if family != 'all':bind('r.family=?', int(family))
        if collector != 'all':bind('r.collector=?', collector)
        if parsed is None:
            bind('r.origin_asn=?', destination_asn);bind('r.members @> ARRAY[?::bigint]', source_asn)
        elif hasattr(parsed, 'prefixlen'):
            bind('r.prefix=?::text::cidr', normalized)
        else:
            bind('r.prefix>>=?::text::inet', normalized)
        # A resource lookup shows the actual route even when the requested ASNs
        # disagree. In particular, LPM happens BEFORE source/origin matching.
        if parsed is not None and not hasattr(parsed, 'prefixlen'):
            base = 'SELECT DISTINCT ON (r.observer) r.*,r.prefix::text prefix_text FROM inet_catalog.routes r WHERE '
            base += ' AND '.join(clauses)+' ORDER BY r.observer,masklen(r.prefix) DESC,r.prefix'
            outer = ''
            if page:
                args.extend(page[1:3]);outer = f' WHERE (observer,prefix)>(${len(args)-1}::text,${len(args)}::text::cidr)'
            args.append(limit+1)
            rows = await pool.fetch('SELECT * FROM ('+base+') selected'+outer+f' ORDER BY observer,prefix LIMIT ${len(args)}', *args)
        else:
            if page:
                args.extend(page[1:3]);clauses.append(f'(r.observer,r.prefix)>(${len(args)-1}::text,${len(args)}::text::cidr)')
            if isinstance(pool, ScopedPool):rows = await pool.page_fetch('routes', clauses, args, limit+1)
            else:
                args.append(limit+1)
                rows = await pool.fetch('SELECT r.*,r.prefix::text prefix_text FROM inet_catalog.routes r WHERE '+' AND '.join(clauses)+f' ORDER BY r.observer,r.prefix LIMIT ${len(args)}', *args)
        more = len(rows)>limit;rows=rows[:limit];items=[]
        for r in rows:
            path=json.loads(r['path']) if isinstance(r['path'],str) else r['path']
            segment=as_segment(path,source_asn,destination_asn)
            if r['origin_asn'] != destination_asn:segment.update(state='origin_mismatch',ordered=False,nodes=[])
            items.append({'observer':r['observer'],'collector':r['collector'],'peer_asn':r['peer_asn'],
                          'prefix':r['prefix_text'],'origin_asn':r['origin_asn'],'observed_at':r['event_at'],
                          'evidence_id':r['evidence_id'],'basis':{0:'RIB snapshot',1:'MRT update',2:'RIS Live'}.get(r['rank'],'unknown'),
                          'segment':segment})
        observers=await read_observers(native)
        observers=[o for o in observers if collector=='all' or o['collector']==collector]
        await ensure_retained(pool,g['id'])
        return {'scope':scope,'generation':public_generation(g),'items':items,'observers':observers,
                'returned_prefixes':len({r['prefix'] for r in items}),
                'next_cursor':encode_cursor(g['id'],rows[-1]['observer'],rows[-1]['prefix_text'],mark) if more else None,
                'resource_basis':'Exact prefix' if hasattr(parsed,'prefixlen') else 'Longest present prefix per viewpoint, before ASN matching' if parsed else 'Source appearance and destination origin within the same route',
                'impact_scope':'Returned route entries only; no global impact denominator or health score.',
                'limitations':LIMITS}
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Observed paths unavailable; no health conclusion can be made') from None


def matching_pairs(row, source, destination, parsed, family):
    evidence=json.loads(row['evidence']) if isinstance(row['evidence'],str) else row['evidence']
    result=[]
    for pair in evidence_pairs(evidence,source,destination):
        prefix=pair.get('prefix') or row['prefix']
        if not prefix:continue
        try:network=ip_network(str(prefix),strict=False)
        except ValueError:continue
        if family!='all' and network.version!=int(family):continue
        if parsed is not None:
            if network.version!=parsed.version:continue
            if hasattr(parsed,'prefixlen') and network!=parsed:continue
            if not hasattr(parsed,'prefixlen') and parsed not in network:continue
        result.append({**pair,'prefix':str(network)})
    return result


@router.get('/history')
async def history(source_asn:int=Query(...,ge=1,le=4294967295),destination_asn:int=Query(...,ge=1,le=4294967295),
                  resource:str|None=Query(None,max_length=49),family:Literal['all','4','6']='all',
                  collector:str=Query('all',max_length=64),minutes:int=Query(60,ge=5,le=20160),
                  until:datetime|None=None,cursor:uuid.UUID|None=None,limit:int=Query(25,ge=1,le=50)):
    validate_collector(collector);parsed=resource_value(resource,family)
    if until is not None:
        if until.tzinfo is None:raise HTTPException(422,'Window end requires UTC or an explicit offset')
        until=until.astimezone(UTC)
        if until>datetime.now(UTC):raise HTTPException(422,'Window end cannot be in the future')
    scope={'source_asn':source_asn,'destination_asn':destination_asn,'resource':str(parsed) if parsed else None,
           'family':family,'collector':collector,'minutes':minutes,'until':until.isoformat() if until else None}
    try:
        pool=await get_pg_pool();cache=await get_redis();hours=await pool.fetchval('SELECT retention_hours FROM routing_diagnostic_policy WHERE id')
        if cursor:
            saved=await cache.get('noc:paths-history:'+str(cursor))
            if not saved:raise HTTPException(409,'Path history page expired; refresh the investigation')
            snap=json.loads(saved)
            if snap['scope']!=scope:raise HTTPException(422,'Path history page belongs to different filters')
            if datetime.now(UTC)>=datetime.fromisoformat(snap['expires_at']) or hours<snap['retention_hours']:
                raise HTTPException(409,'Path history page expired or retention changed')
        else:
            now=datetime.now(UTC);heads=await pool.fetch('SELECT observer,sequence FROM routing_diagnostic_cursors')
            snap={'scope':scope,'as_of':now.isoformat(),'expires_at':(now+timedelta(seconds=PAGE_TTL)).isoformat(),
                  'retention_hours':hours,'heads':{r['observer']:r['sequence'] for r in heads}}
        captured=datetime.fromisoformat(snap['as_of']);through=until or captured
        requested_from=through-timedelta(minutes=minutes);floor=captured-timedelta(hours=snap['retention_hours'])
        lower=max(requested_from,floor);outside=through<=floor
        args=[lower,through,json.dumps(snap['heads']),[source_asn,destination_asn]]
        clauses=['e.event_at>$1','e.event_at<=$2',"e.sequence<=COALESCE(($3::jsonb->>e.observer)::bigint,-1)",'e.involved_asns @> $4::bigint[]']
        if collector!='all':args.append(collector);clauses.append(f'o.collector=${len(args)}')
        if snap.get('after'):
            args.extend([datetime.fromisoformat(snap['after'][0]),snap['after'][1]])
            clauses.append(f'(e.event_at,e.id)<(${len(args)-1},${len(args)})')
        select='''SELECT e.id,e.event_id::text,e.observer,e.sequence,e.epoch,e.event_at,e.received_at,
          e.prefix::text prefix,e.kind,e.candidate,e.origin_asn,e.expected_asn,e.evidence,o.collector,o.peer_asn
          FROM routing_diagnostic_events e JOIN routing_observers o ON o.id=e.observer WHERE '''+' AND '.join(clauses)
        budget=250
        if parsed is not None:
            args.append(str(parsed));param='$'+str(len(args));op='=' if hasattr(parsed,'prefixlen') else '>>='
            cast='cidr' if hasattr(parsed,'prefixlen') else 'inet'
            ordinary=select+f" AND e.candidate IS DISTINCT FROM 'withdrawal_surge' AND e.prefix {op} {param}::text::{cast}"
            surge=select+" AND e.candidate='withdrawal_surge' AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(e.evidence->'facts'->'distinct_prefixes','[]'::jsonb)) p WHERE p::cidr "+op+f' {param}::text::{cast})'
            args.append(budget+1);order=f' ORDER BY e.event_at DESC,e.id DESC LIMIT ${len(args)}'
            query='('+ordinary+order+') UNION ALL ('+surge+order+f') ORDER BY event_at DESC,id DESC LIMIT ${len(args)}'
        else:
            args.append(budget+1);query=select+f' ORDER BY e.event_at DESC,e.id DESC LIMIT ${len(args)}'
        raw=[] if outside else await pool.fetch(query,*args)
        items=[];last=None;scanned=0;more=False;matched_prefixes=set()
        for row in raw[:budget]:
            scanned+=1;pair_matches=matching_pairs(row,source_asn,destination_asn,parsed,family)
            if pair_matches:
                if len(items)==limit:more=True;break
                item=history_item(row);proof=item['evidence'];facts=proof.get('facts') or {}
                matched_prefixes.update(m['prefix'] for m in pair_matches)
                item['pair_matches']=pair_matches[:20];item['pair_matches_capped']=len(pair_matches)>20
                item['matching_prefixes']=len({m['prefix'] for m in pair_matches})
                item['evidence']={'before':proof.get('before'),'after':proof.get('after'),
                  'facts':{k:v for k,v in facts.items() if v is None or isinstance(v,(str,int,float,bool))}}
                items.append(item)
            last=row
        if len(raw)>budget:more=True
        if datetime.now(UTC)>=datetime.fromisoformat(snap['expires_at']):raise HTTPException(409,'Path history page expired; refresh')
        next_cursor=None
        if more and last:
            snap['after']=[last['event_at'].isoformat(),last['id']];next_cursor=str(uuid.uuid4())
            ttl=max(1,int((datetime.fromisoformat(snap['expires_at'])-datetime.now(UTC)).total_seconds()))
            await cache.setex('noc:paths-history:'+next_cursor,ttl,json.dumps(snap))
        gaps=await pool.fetch('''SELECT observer,opened_at,from_at,until_at,reason,state,repaired_at
          FROM routing_gaps g JOIN routing_observers o ON o.id=g.observer
          WHERE g.observer=ANY($1::text[]) AND ($2='all' OR o.collector=$2)
          AND COALESCE(from_at,opened_at)<=$4 AND COALESCE(until_at,clock_timestamp())>=$3
          ORDER BY COALESCE(from_at,opened_at) DESC LIMIT 101''',list(snap['heads']),collector,lower,through) if not outside else []
        candidates={kind:sum(i['candidate']==kind for i in items) for kind in ['bgp_flap','bgp_hijack','withdrawal_surge']}
        return {'scope':scope,'as_of':snap['as_of'],'expires_at':snap['expires_at'],'items':items,
                'next_cursor':next_cursor,'scanned_rows':scanned,'scan_budget':budget,
                'requested_window':{'from':requested_from,'through':through},'effective_window':None if outside else {'from':lower,'through':through},
                'retention_clipped':requested_from<floor,'outside_retention':outside,
                'coverage':await read_status(pool,collector),'history_gaps':[dict(g) for g in gaps[:100]],'history_gaps_capped':len(gaps)>100,
                'page_impact':{'prefixes':len(matched_prefixes),'viewpoints':len({i['observer'] for i in items}),'candidates':candidates,
                               'scope':'This returned page only. Prefixes may overlap and candidates may share lineage. Global affected population is unknown.'},
                'limitations':LIMITS+['Source-level gaps may overlap this period; affected ASN/prefix impact is unknown even if a RIB was later repaired.',
                    'A short or empty page can have further candidates to inspect; use Next when available. The scan is bounded and pagination never extends its five-minute lifetime.']}
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Path history unavailable; no health conclusion can be made') from None
