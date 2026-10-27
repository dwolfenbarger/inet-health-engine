"""Deterministic event-time diagnostics for one continuous observer.

No probability, reachability or hijack confirmation is inferred. The caller
commits state, evidence and its journal cursor in one transaction.
"""
from copy import deepcopy
WINDOW = 300
FLAP_TRANSITIONS = 25
ORIGIN_BASELINE = 300
STATE_TTL = 86400


def apply_change(previous, change, epoch):
    at = change['event_at'].timestamp()
    old = deepcopy(previous) if previous else None
    baseline_reason='first_observation'
    if old and old['epoch']!=epoch:baseline_reason='continuity_reset'
    elif old and at-old['at']>STATE_TTL:baseline_reason='state_expired'
    if old and (old['epoch'] != epoch or at-old['at'] > STATE_TTL):
        old = None
    if old and at < old['at']:
        return old, [], False
    current = {'present':change['present'], 'origin':change['origin_asn'], 'path':change['path']}
    events = []
    def emit(kind, candidate=None, **facts):
        events.append({'kind':kind, 'candidate':candidate,
            'before':{k:old[k] for k in ['present','origin','path','at']} if old else None,
            'after':current, 'facts':facts})
    if old is None:
        state = {'epoch':epoch,'at':at,**current,'origin_since':at,
                 'last_origin':current['origin'],'last_path':current['path'],'last_announce_at':at if current['present'] else None,
                 'transitions':[], 'flap_reported_at':None}
        emit('baseline', baseline='First observation after activation, gap, reset or state expiry',baseline_reason=baseline_reason)
        return state, events, True
    state = deepcopy(old)
    state['transitions'] = [t for t in old['transitions'] if at-WINDOW < t <= at]
    changed_presence = old['present'] != current['present']
    changed_path = old['present'] and current['present'] and old['path'] != current['path']
    if changed_presence or changed_path:
        state['transitions'].append(at)
    if changed_presence:
        emit('announce' if current['present'] else 'withdraw')
    if current['present']:
        if old.get('last_origin') and current['origin'] and old['last_origin'] != current['origin']:
            stable = max(0, at-old['origin_since'])
            emit('origin_change', 'bgp_hijack' if stable >= ORIGIN_BASELINE else None,
                 expected_asn=old['last_origin'], previous_announced_path=old['last_path'], previous_announced_at=old.get('last_announce_at'),
                 baseline_seconds=stable, minimum_baseline_seconds=ORIGIN_BASELINE)
            state['origin_since'] = at
        elif not old.get('last_origin') or not current['origin']:
            state['origin_since'] = at
        if old['present'] and old['path'] != current['path'] and not any(e['kind']=='origin_change' for e in events):
            emit('path_change')
        state['last_announce_at'] = at
        state['last_origin'] = current['origin']
        state['last_path'] = current['path']
    if (changed_presence or changed_path) and len(state['transitions']) >= FLAP_TRANSITIONS and (
        state['flap_reported_at'] is None or at-state['flap_reported_at'] >= WINDOW):
        emit('flap_threshold','bgp_flap',transitions=len(state['transitions']),
             transition_scope='Observed presence or AS-path changes; identical reannouncements excluded',
             transition_count_lower_bound=len(state['transitions'])>=128,window_seconds=WINDOW,
             expected_asn=old.get('last_origin'))
        state['flap_reported_at'] = at
    state['transitions'] = state['transitions'][-128:]
    state.update(current);state['at'] = at
    return state, events, True


def apply_withdrawal(previous, prefix, at, epoch, before_path=None):
    """200 distinct known-present prefixes withdrawn by one observer/origin in 60s."""
    old=deepcopy(previous) if previous and previous.get('epoch')==epoch else {}
    if old and at < old.get('at',at):return old,None
    members={p:t for p,t in old.get('members',{}).items() if at-60<t<=at}
    paths={p:path for p,path in old.get('paths',{}).items() if p in members}
    members[prefix]=at
    if before_path is not None:paths[prefix]=deepcopy(before_path)
    count=len(members);reported=old.get('reported_at')
    capped_until=old.get('capped_until',0)
    ordered=sorted(members.items(),key=lambda x:x[1])
    if count>256:capped_until=max(capped_until,max(t for _,t in ordered[:-256])+60)
    event=None
    if count>=200 and (reported is None or at-reported>=60):
        event={'withdrawal_count':count,'withdrawal_count_lower_bound':count>=256 or capped_until>at,'window_seconds':60,'distinct_prefixes':list(members),
               'prefixes_capped':count>=256 or capped_until>at,
               'withdrawn_prefix_paths':[{'prefix':p,'as_path':paths[p]} for p in members if p in paths],
               'recorded_paths_complete':all(p in paths for p in members),
               'count_scope':'Distinct prefixes observed transitioning from present to withdrawn'}
        reported=at
    # More recent members preserve threshold utility with a strict per-AS bound.
    members=dict(ordered[-256:])
    return {'epoch':epoch,'at':at,'members':members,'paths':{p:paths[p] for p in members if p in paths},'reported_at':reported,'capped_until':capped_until if capped_until>at else 0},event
