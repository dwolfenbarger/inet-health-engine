"""Continuous selected-peer diagnostics; observed changes are not confirmed incidents."""
import json
import uuid
from datetime import datetime, timezone, timedelta
from ipaddress import ip_address, ip_network
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from api.deps import get_pg_pool, get_redis
from api.routes_coverage import decode

router=APIRouter(prefix='/diagnostics',tags=['diagnostics'])
LIMITS=[
 'Continuous analysis covers only the named selected RIS peers, not the global Internet or all routes.',
 'History begins at detector activation. First observations after activation, a gap, reset or state expiry warm a baseline; they are not changes.',
 'Source gaps remain unobserved by this detector even when a separate routing archive repairs a RIB. Recovery is not inferred.',
 'AS paths are BGP control-plane observations, not traceroutes, measured latency or proof of reachability.',
 'Origin-change, flap and withdrawal thresholds produce unconfirmed candidates. Legitimate routing changes can trigger them.',
 'Sampled and continuous RIS evidence share source lineage and may overlap; they are not independent confirmations.',
]
TITLES={'bgp_flap':'Route flapping','bgp_hijack':'Suspected origin hijack','withdrawal_surge':'Withdrawal surge'}
PAGE_TTL=300


def resource_value(resource):
    if not resource:return None
    try:return ip_network(resource,strict=False) if '/' in resource else ip_address(resource)
    except ValueError:raise HTTPException(422,'Enter an IPv4/IPv6 prefix or IP address') from None


def filters(args,asn,resource,family,collector):
    clauses=[]
    def bind(value):args.append(value);return '$'+str(len(args))
    asn_param=bind(asn) if asn else None
    if asn_param:clauses.append(f'e.involved_asns @> ARRAY[{asn_param}::bigint]')
    normal=[];surge=[]
    if resource:
        # Prefix searches include more specifics; IP searches include covering routes.
        op='<<=' if hasattr(resource,'prefixlen') else '>>='
        param=bind(str(resource))
        normal.append(f'e.prefix {op} {param}::text::inet')
        surge.append(f'p::cidr {op} {param}::text::inet')
    if family!='all':
        param=bind(int(family));normal.append(f'family(e.prefix)={param}');surge.append(f'family(p::cidr)={param}')
    if normal:
        matching=' AND '.join(surge)
        specific=''
        if asn_param:
            paths=matching.replace('p::cidr',"(w->>'prefix')::cidr")
            specific=f" AND (e.origin_asn={asn_param} OR e.expected_asn={asn_param} OR o.peer_asn={asn_param} OR EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(e.evidence->'facts'->'withdrawn_prefix_paths','[]'::jsonb)) w WHERE {paths} AND jsonb_path_exists(w->'as_path','$.** ? (@ == $asn)',jsonb_build_object('asn',{asn_param}::bigint))))"
        clauses.append("((e.candidate IS DISTINCT FROM 'withdrawal_surge' AND "+' AND '.join(normal)+") OR (e.candidate='withdrawal_surge' AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(e.evidence->'facts'->'distinct_prefixes','[]'::jsonb)) p WHERE "+matching+')'+specific+'))')
    if collector!='all':clauses.append(f'o.collector={bind(collector)}')
    return clauses


async def read_status(pool,collector='all'):
    now=datetime.now(timezone.utc)
    policy=await pool.fetchrow('SELECT * FROM routing_diagnostic_policy WHERE id')
    rows=await pool.fetch('''SELECT o.id observer,o.collector,o.peer_asn,host(o.peer_ip) peer_ip,
       o.sequence intake_sequence,o.event_at intake_event_at,o.received_at intake_received_at,o.stream_state intake_stream_state,
       c.sequence,c.epoch,c.started_at,c.received_at,c.event_at,c.heartbeat_at,c.status,c.error_code,c.skipped_late,c.continuity,
       (SELECT count(*) FROM routing_gaps g WHERE g.observer=o.id AND g.opened_at>now()-interval '24 hours') recent_source_gaps
       FROM routing_observers o LEFT JOIN routing_diagnostic_cursors c ON c.observer=o.id
       WHERE o.mode='ris-live' AND ($1='all' OR o.collector=$1) ORDER BY o.id''',collector)
    observers=[]
    for row in rows:
        r=dict(row);r['continuity']=decode(r.get('continuity')) or {};r['backlog']=max(0,r['intake_sequence']-r['sequence']) if r['sequence'] is not None else None
        r['input_state']='fresh' if r['intake_received_at'] and (now-r['intake_received_at']).total_seconds()<=180 else 'stale'
        if r.get('intake_stream_state','connected')!='connected':r['input_state']=r['intake_stream_state']
        r['consumer_state']='not_started' if r['sequence'] is None else 'stale' if not r['heartbeat_at'] or (now-r['heartbeat_at']).total_seconds()>90 else r['status']
        r['processing_lag_seconds']=max(0,(r['intake_received_at']-r['received_at']).total_seconds()) if r['intake_received_at'] and r['received_at'] else None
        if r['consumer_state'] in ['waiting','processing'] and r['processing_lag_seconds'] is not None and r['processing_lag_seconds']>60:r['consumer_state']='catching_up'
        observers.append(r)
    gaps=await pool.fetch('''SELECT g.observer,g.opened_at,g.from_at,g.until_at,g.reason,g.state,g.repaired_at FROM routing_gaps g
       JOIN routing_observers o ON o.id=g.observer WHERE o.mode='ris-live' AND ($1='all' OR o.collector=$1)
       AND g.opened_at>now()-interval '24 hours' ORDER BY g.opened_at DESC LIMIT 51''',collector)
    p=dict(policy) if policy else {'enabled':False,'state':'not_installed'}
    state='disabled' if not p['enabled'] else p['state']
    if p['enabled'] and (not p.get('checked_at') or (now-p['checked_at']).total_seconds()>90):state='stale'
    if state=='running':
        if not observers:state='not_covered'
        elif any(r['consumer_state']=='error' for r in observers):state='degraded'
        elif any(r['consumer_state']=='stale' for r in observers):state='stale'
        elif any(r['consumer_state']=='not_started' for r in observers):state='warming'
        elif any(r['input_state']=='stale' for r in observers):state='input_stale'
        elif any(r['input_state']!='fresh' for r in observers):state='input_unavailable'
        elif any(r['consumer_state']=='catching_up' for r in observers):state='catching_up'
    if state=='running' and p.get('retention_mode')=='partitioned':
        if p.get('retention_error') or not p.get('retention_checked_at') or (now-p['retention_checked_at']).total_seconds()>3600:state='retention_degraded'
    return {'state':state,'as_of':now,'policy':p,'observers':observers,'recent_gaps':[dict(g) for g in gaps[:50]],
        'gaps_capped':len(gaps)>50,'gap_scope':'Source-level gaps for selected peers in the last 24 hours; affected ASN/prefix scope is unknown.',
        'limits':LIMITS}


async def candidate_groups(pool,minutes,asn,resource,family,collector,now):
    args=[now-timedelta(minutes=minutes),now]
    clauses=['e.event_at>$1','e.event_at<=$2','e.candidate IS NOT NULL']+filters(args,asn,resource,family,collector)
    rows=await pool.fetch('''SELECT e.candidate,e.prefix::text affected_prefix,e.origin_asn,e.expected_asn,
       min(e.event_at) first_seen,max(e.event_at) last_seen,count(*) reports,count(DISTINCT e.observer) viewpoints,
       array_agg(DISTINCT ('ris-continuous/'||o.collector||'/AS'||o.peer_asn)) sources,
       (array_agg(e.event_id::text ORDER BY e.event_at DESC,e.id DESC))[1] latest_id
       FROM routing_diagnostic_events e JOIN routing_observers o ON o.id=e.observer WHERE '''+' AND '.join(clauses)+'''
       GROUP BY e.candidate,e.prefix,e.origin_asn,e.expected_asn ORDER BY max(e.event_at) DESC''',*args)
    result=[]
    for row in rows:
        x=dict(row);x['id']='diag:'+x.pop('latest_id');x['kind']=x.pop('candidate');x['title']=TITLES[x['kind']]
        x.update(evidence_type='routing',detection_mode='continuous',state='candidate',severity=None,detector_confidence=None,
                 confirmation='Unconfirmed; recovery not established')
        for k in ['first_seen','last_seen']:x[k]=x[k].isoformat()
        result.append(x)
    return result


@router.get('/status')
async def status():
    try:return await read_status(await get_pg_pool())
    except Exception:raise HTTPException(503,'Continuous diagnostic status unavailable') from None


def history_item(row):
    r=dict(row);r['evidence']=decode(r['evidence']);return r


@router.get('/history')
async def history(asn:int|None=Query(None,ge=1,le=4294967295),resource:str|None=Query(None,max_length=49),
    minutes:int=Query(60,ge=5,le=20160),family:Literal['all','4','6']='all',collector:str=Query('all',max_length=64),
    cursor:uuid.UUID|None=None,limit:int=Query(50,ge=1,le=100),until:datetime|None=None):
    requested=resource_value(resource)
    if not asn and not requested:raise HTTPException(422,'Choose an ASN, prefix or IP for routing history')
    from api.routes_noc import COLLECTORS
    if collector!='all' and collector not in COLLECTORS:raise HTTPException(422,'Unknown collector')
    if until is not None:
        if until.tzinfo is None:raise HTTPException(422,'Window end requires UTC or an explicit offset')
        until=until.astimezone(timezone.utc)
        if until>datetime.now(timezone.utc):raise HTTPException(422,'Window end cannot be in the future')
    scope={'asn':asn,'resource':str(requested) if requested else None,'minutes':minutes,'family':family,'collector':collector,'until':until.isoformat() if until else None}
    try:
        pool=await get_pg_pool();cache=await get_redis()
        policy=await pool.fetchrow('SELECT retention_hours FROM routing_diagnostic_policy WHERE id')
        retention_hours=policy['retention_hours'] if policy else 24
        if cursor:
            saved=await cache.get('noc:history:'+str(cursor))
            if not saved:raise HTTPException(409,'History page expired. Refresh history.')
            snap=json.loads(saved)
            if {**snap['scope'],'until':snap['scope'].get('until')}!=scope:raise HTTPException(422,'History page belongs to different filters')
            if datetime.now(timezone.utc)>=datetime.fromisoformat(snap['expires_at']) or retention_hours<snap['retention_hours']:
                raise HTTPException(409,'History page expired or retention changed. Refresh history.')
        else:
            # Committed observer checkpoints fence every page. In-flight lower SQL
            # sequence IDs cannot appear later inside this snapshot.
            heads=await pool.fetch('SELECT observer,sequence FROM routing_diagnostic_cursors')
            now=datetime.now(timezone.utc)
            snap={'scope':scope,'as_of':now.isoformat(),'expires_at':(now+timedelta(seconds=PAGE_TTL)).isoformat(),'retention_hours':retention_hours,'heads':{r['observer']:r['sequence'] for r in heads}}
        now=datetime.fromisoformat(snap['as_of'])
        through=until or now
        requested_from=through-timedelta(minutes=minutes)
        floor=now-timedelta(hours=snap['retention_hours'])
        effective_from=max(requested_from,floor)
        # An old window remains explicitly empty; it never expands to current data.
        outside=through<=floor
        args=[effective_from,through,json.dumps(snap['heads'])]
        clauses=['e.event_at>$1','e.event_at<=$2',"e.sequence<=COALESCE(($3::jsonb->>e.observer)::bigint,-1)"]+filters(args,asn,requested,family,collector)
        if snap.get('after'):
            args.extend([datetime.fromisoformat(snap['after'][0]),snap['after'][1]])
            clauses.append(f'(e.event_at,e.id)<(${len(args)-1},${len(args)})')
        args.append(limit+1)
        select='''SELECT e.id,e.event_id::text,e.observer,e.sequence,e.epoch,e.event_at,e.received_at,
           e.prefix::text prefix,e.kind,e.candidate,e.origin_asn,e.expected_asn,e.evidence,o.collector,o.peer_asn
           FROM routing_diagnostic_events e JOIN routing_observers o ON o.id=e.observer WHERE '''+' AND '.join(clauses)
        if requested:
            # Keep resource-indexable rows separate from the aggregate withdrawal
            # evidence. An OR with JSON prefix expansion otherwise scans history.
            # Both branches retain the same complete ASN/resource correlation filter.
            args.append(str(requested));resource_param='$'+str(len(args))
            op='<<=' if hasattr(requested,'prefixlen') else '>>='
            order=f' ORDER BY e.event_at DESC,e.id DESC LIMIT ${len(args)-1}'
            query='('+select+f" AND e.candidate IS DISTINCT FROM 'withdrawal_surge' AND e.prefix {op} {resource_param}::text::inet"+order+') UNION ALL ('+select+" AND e.candidate='withdrawal_surge'"+order+f') ORDER BY event_at DESC,id DESC LIMIT ${len(args)-1}'
        else:query=select+f' ORDER BY e.event_at DESC,e.id DESC LIMIT ${len(args)}'
        rows=await pool.fetch(query,*args)
        next_cursor=None
        if datetime.now(timezone.utc)>=datetime.fromisoformat(snap['expires_at']):
            raise HTTPException(409,'History page expired. Refresh history.')
        if len(rows)>limit:
            last=rows[limit-1];snap['after']=[last['event_at'].isoformat(),last['id']];next_cursor=str(uuid.uuid4())
            ttl=max(1,int((datetime.fromisoformat(snap['expires_at'])-datetime.now(timezone.utc)).total_seconds()))
            await cache.setex('noc:history:'+next_cursor,ttl,json.dumps(snap))
        history_gaps=await pool.fetch('''SELECT g.observer,g.opened_at,g.from_at,g.until_at,g.reason,g.state,g.repaired_at
          FROM routing_gaps g JOIN routing_observers o ON o.id=g.observer
          WHERE g.observer=ANY($1::text[]) AND ($2='all' OR o.collector=$2)
          AND COALESCE(g.from_at,g.opened_at)<=$4 AND COALESCE(g.until_at,clock_timestamp())>=$3
          ORDER BY COALESCE(g.from_at,g.opened_at) DESC LIMIT 101''',list(snap['heads']),collector,effective_from,through) if not outside else []
        return {'history_gaps':[dict(g) for g in history_gaps[:100]],'history_gaps_capped':len(history_gaps)>100,
            'history_gap_scope':'Source-level gaps overlapping this historical period; affected ASN/prefix scope is unknown. Gap repair status is current, independent of the pinned history snapshot.',
            'as_of':now,'scope':scope,'requested_window':{'from':requested_from,'through':through},'effective_window':None if outside else {'from':effective_from,'through':through},'retention_clipped':requested_from<floor,'outside_retention':outside,'items':[history_item(r) for r in rows[:limit]],'next_cursor':next_cursor,
            'coverage':await read_status(pool,collector),'limits':LIMITS+['Pages hold committed observer checkpoints for five minutes from the first page. Retention cleanup protects that window; expired pages require refresh.']}
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Routing history unavailable; no health conclusion can be made') from None


@router.get('/detail')
async def detail(event_id:uuid.UUID):
    try:
        pool=await get_pg_pool()
        row=await pool.fetchrow('''SELECT e.*,e.prefix::text prefix_text,o.collector,o.peer_asn FROM routing_diagnostic_events e
          JOIN routing_observers o ON o.id=e.observer WHERE e.event_id=$1''',event_id)
        if not row:raise HTTPException(404,'This continuous observation is no longer retained')
        proof=decode(row['evidence']);before=proof.get('before') or {};after=proof.get('after') or {};facts=proof.get('facts') or {}
        raw={**facts,'as_path':after.get('path') if after.get('present') else before.get('path'),
             'peer_asn':row['peer_asn'],'before':before,'after':after,'journal_sequence':row['sequence'],'epoch':row['epoch'],
             'received_at':row['received_at'],'message_id':proof.get('message_id')}
        item={'event_id':str(event_id),'event_type':row['candidate'] or row['kind'],'affected_prefix':row['prefix_text'],
            'origin_asn':row['origin_asn'],'expected_asn':row['expected_asn'],'time':row['event_at'],
            'source':'ris-continuous/'+row['collector'],'severity':None,'confidence':None,'raw_data':raw}
        earlier=None;previous_at=facts.get('previous_announced_at')
        if previous_at and facts.get('previous_announced_path'):
            earlier={'time':datetime.fromtimestamp(previous_at,timezone.utc),'origin_asn':row['expected_asn'],
                     'as_path':facts['previous_announced_path'],'change_type':'announce','rpki_status':'unknown'}
        history_rows=await pool.fetch('''SELECT event_at,kind,evidence FROM routing_diagnostic_events
          WHERE observer=$1 AND prefix=$2 AND event_at BETWEEN $3 AND $4 ORDER BY event_at DESC,id DESC LIMIT 101''',
          row['observer'],row['prefix'],row['event_at']-timedelta(minutes=15),row['event_at']+timedelta(minutes=5)) if row['prefix'] else []
        updates=[]
        for h in history_rows[:100]:
            ev=decode(h['evidence']);v=ev.get('after') or {}
            updates.append({'time':h['event_at'],'change_type':h['kind'],'origin_asn':v.get('origin'),
                            'as_path':v.get('path'),'rpki_status':'unknown'})
        validation=None
        if row['prefix_text'] and row['origin_asn']:
            v=await pool.fetchrow("SELECT observed_at,payload FROM source_evidence WHERE source='rpki-local' AND evidence_id=$1 AND observed_at>now()-interval '60 minutes'",f"{row['prefix_text']}|{row['origin_asn']}")
            if v:validation={'observed_at':v['observed_at'],'evidence':decode(v['payload'])}
        return {'detection_mode':'continuous','candidate':item,'updates':updates,'updates_capped':len(history_rows)>100,
            'previous_origin_announcement':earlier,'current_origin_validation':validation,
            'scope':'Retained state changes for this exact prefix and peer: 15 minutes before through 5 minutes after; latest 100. Repeated identical announcements do not create change rows.',
            'limits':LIMITS}
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Continuous observation detail unavailable') from None
