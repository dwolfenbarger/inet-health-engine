"""Qualified correlation, page impact and explicit absence of evidence."""
from datetime import datetime,timezone
from ipaddress import ip_address,ip_network
from api.path_evidence import path_nodes,valid_asn


def decoded_path(value):
    return value.get('path') if isinstance(value,dict) and value.get('present') else None


def ordered_asns(path):
    return {n['asn'] for n in path_nodes(path) if n.get('kind')=='asn'} if path else set()


def change_summary(proof):
    before=decoded_path(proof.get('before'));after=decoded_path(proof.get('after'))
    a,b=ordered_asns(before),ordered_asns(after)
    return {'before_path':before,'after_path':after,'appeared_asns':sorted(b-a),
            'disappeared_asns':sorted(a-b),'path_changed':before!=after,
            'scope':'One recorded transition. Missing paths do not establish router disappearance or forwarding failure.'}


def compact_event(row):
    item=dict(row);proof=item.get('evidence') or {};facts=proof.get('facts') or {}
    item['evidence']={'before':proof.get('before'),'after':proof.get('after'),
      'facts':{k:v for k,v in facts.items() if v is None or isinstance(v,(str,int,float,bool))}}
    prefixes=list(dict.fromkeys(str(p) for p in facts.get('distinct_prefixes',[])))
    item['recorded_prefixes']=prefixes[:100];item['recorded_prefixes_capped']=len(prefixes)>100
    item['recorded_prefix_count']=len(prefixes);item['change']=change_summary(proof)
    involved=ordered_asns(item['change']['before_path'])|ordered_asns(item['change']['after_path'])
    withdrawn=facts.get('withdrawn_prefix_paths',[])
    for entry in withdrawn[:100]:involved.update(ordered_asns(entry.get('as_path')))
    item['recorded_asns']=sorted(involved)[:128];item['recorded_asns_capped']=len(involved)>128 or len(withdrawn)>100
    item['display_evidence_scope']='Bounded display; full original evidence remains available from the retained observation endpoint.'
    item['raw_evidence_url']='/api/v1/noc/diagnostics/detail?event_id='+str(item['event_id'])
    return item


def scoped_event(row,resource=None,asn=None,family='all'):
    """Scope aggregate impact by each recorded prefix/path, never array overlap."""
    item=dict(row);proof=dict(item.get('evidence') or {});facts=dict(proof.get('facts') or {})
    parsed=ip_network(resource,strict=False) if resource and '/' in resource else ip_address(resource) if resource else None
    def matches(prefix):
        try:
            network=ip_network(prefix,strict=False)
            if family!='all' and network.version!=int(family):return False
            if parsed is None:return True
            if network.version!=parsed.version:return False
            return network.subnet_of(parsed) if hasattr(parsed,'prefixlen') else parsed in network
        except (TypeError,ValueError):return False
    paths=facts.get('withdrawn_prefix_paths',[])
    if paths:
        selected=[p for p in paths if matches(p.get('prefix')) and (not asn or asn in ordered_asns(p.get('as_path')))]
        facts['withdrawn_prefix_paths']=selected
        facts['distinct_prefixes']=list(dict.fromkeys(p['prefix'] for p in selected))
    elif facts.get('distinct_prefixes'):
        facts['distinct_prefixes']=[p for p in facts['distinct_prefixes'] if matches(p)] if not asn else []
        facts['prefix_asn_mapping_unknown']=bool(asn)
    proof['facts']=facts;item['evidence']=proof
    return compact_event(item)


def page_impact(items):
    prefixes=set();asns=set();observers=set();flags={};changes=0
    for e in items:
        observers.add(e['observer'])
        if e.get('prefix'):prefixes.add(str(e['prefix']))
        prefixes.update(e.get('recorded_prefixes',[]))
        asns.update(e.get('recorded_asns',[]))
        if e['kind'] not in ['baseline','gap','reset','state_expired']:changes+=1
        if e.get('candidate'):flags[e['candidate']]=flags.get(e['candidate'],0)+1
    return {'observations':len(items),'recorded_changes':changes,'prefixes':len(prefixes),'viewpoints':len(observers),
            'prefixes_lower_bound':any(e.get('recorded_prefixes_capped') for e in items),
            'asns':sorted(asns)[:128],'asns_capped':len(asns)>128 or any(e.get('recorded_asns_capped') for e in items),'candidate_flags':flags,
            'scope':'This timeline page only; prefixes can overlap and reports share RIS lineage. Baselines are not changes. Global affected population and recovery are unknown.'}


def measurement_match(direction,resource,asn,lower,through):
    at=direction.get('observed_at')
    if not at:return None
    try:
        clock=datetime.fromisoformat(at)
        if clock.tzinfo is None or not lower<=clock<=through:return None
        af=direction['pair']['family'];target=ip_address(direction['target'][f'ip_v{af}'])
        if resource:
            parsed=ip_network(resource,strict=False) if '/' in resource else ip_address(resource)
            same=(target.version==parsed.version and target in parsed) if hasattr(parsed,'prefixlen') else target==parsed
            if not same:return None
            basis='same_target_prefix_and_time' if hasattr(parsed,'prefixlen') else 'same_target_ip_and_time'
        elif asn and asn in [direction['source'].get(f'as_v{af}'),direction.get('target_asn')]:basis='same_endpoint_asn_and_time'
        else:return None
    except (ValueError,KeyError,TypeError):return None
    return {**direction,'correlation_basis':basis,
      'correlation_limit':'Time/resource overlap only. An endpoint measurement is not ASN-wide performance, an independent confirmation of each RIS report, or a located cause.'}


def coverage_action(state,mode):
    if state in ['continuous','archive']:
        return 'Compare a second independent viewpoint and inspect historical source gaps.' if mode=='ris-live' else 'Archive-delayed view: compare a continuous peer before judging a transient event.'
    if state=='gap':return 'Review missing intervals and recovery state; reconnecting does not restore diagnostic history.'
    if state in ['snapshot_required','awaiting_replay','recovery_pending']:return 'Wait for a qualified baseline/replay; current route absence is inconclusive.'
    return 'Check source connection, event time and processing lag before drawing a network-health conclusion.'
