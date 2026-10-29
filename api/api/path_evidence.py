"""Exact route and traceroute observations. Never joins separate paths or invents hops."""
import math
import statistics
from datetime import datetime, timezone
from ipaddress import ip_address

UTC = timezone.utc


def valid_asn(value):
    return type(value) is int and 1 <= value <= 4294967295


def path_nodes(path):
    if not isinstance(path, list):
        return [{'kind': 'unparsed', 'raw': path}]
    nodes = []
    for item in path:
        if valid_asn(item):
            if nodes and nodes[-1]['kind'] == 'asn' and nodes[-1]['asn'] == item:
                nodes[-1]['repetitions'] += 1
            else:
                nodes.append({'kind': 'asn', 'asn': item, 'repetitions': 1})
        elif isinstance(item, list) and item and all(valid_asn(a) for a in item):
            nodes.append({'kind': 'as_set', 'asns': item})
        elif isinstance(item, dict) and 'confederation' in item:
            nodes.append({'kind': 'confederation', 'raw': item['confederation']})
        else:
            nodes.append({'kind': 'unknown', 'raw': item})
    return nodes


def as_segment(path, source, destination):
    """A suffix of one advertised AS path, never a source router's selected FIB."""
    nodes = path_nodes(path)
    result = {'raw_path': path, 'full_nodes': nodes, 'nodes': [], 'state': 'origin_mismatch',
              'source_occurrences': 0, 'transit_asns': [], 'ordered': False}
    if not nodes or nodes[-1].get('asn') != destination:
        return result
    positions = [i for i, n in enumerate(nodes) if n.get('asn') == source]
    result['source_occurrences'] = len(positions)
    if not positions:
        result['state'] = 'source_not_ordered' if any(source in n.get('asns', []) for n in nodes) else 'source_not_observed'
        return result
    selected = nodes[positions[0]:]
    result.update(nodes=selected, state='observed_segment', ordered=True)
    if len(positions) > 1:
        result.update(state='source_loop_ambiguous', ordered=False)
    elif any(n['kind'] != 'asn' for n in selected):
        result.update(state='ambiguous_segment', ordered=False)
    else:
        values = [n['asn'] for n in selected]
        if len(set(values)) != len(values):
            result.update(state='path_loop_ambiguous', ordered=False)
        result['transit_asns'] = values[1:-1]
    return result


def evidence_pairs(evidence, source, destination):
    """Both ASNs must occur in the SAME recorded prefix path, in order."""
    matches = []
    for side in ['before', 'after']:
        value = evidence.get(side) or {}
        if value.get('present'):
            segment = as_segment(value.get('path'), source, destination)
            if segment['state'] not in ['origin_mismatch', 'source_not_observed', 'source_not_ordered']:
                matches.append({'side': side, 'segment': segment})
    facts = evidence.get('facts') or {}
    for row in facts.get('withdrawn_prefix_paths', []):
        segment = as_segment(row.get('as_path'), source, destination)
        if segment['state'] not in ['origin_mismatch', 'source_not_observed', 'source_not_ordered']:
            matches.append({'side': 'withdrawn', 'prefix': row.get('prefix'), 'segment': segment})
    return matches


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid measurement number')
    return value


def trace_observation(raw, metadata, probe, lower, through):
    if (type(raw.get('msm_id')) is not int or raw['msm_id'] != metadata['id'] or
            type(raw.get('prb_id')) is not int or raw['prb_id'] != probe['id'] or
            raw.get('type') != 'traceroute' or raw.get('af') != metadata['af']):
        raise ValueError('Traceroute identity mismatch')
    at = datetime.fromtimestamp(finite_number(raw.get('timestamp')), UTC)
    if not lower <= at <= through:
        raise ValueError('Traceroute outside requested window')
    target = str(ip_address(metadata['target_ip']))
    if str(ip_address(raw['dst_addr'])) != target:
        raise ValueError('Traceroute destination mismatch')
    if ip_address(target).version != metadata['af']:
        raise ValueError('Traceroute family mismatch')
    hops = raw.get('result', [])
    if not isinstance(hops, list) or len(hops) > 255:
        raise ValueError('Traceroute hop budget exceeded')
    by_ttl = {}
    destination_replied = False
    for hop in hops:
        ttl = hop.get('hop')
        if type(ttl) is not int or not 1 <= ttl <= 255 or ttl in by_ttl:
            raise ValueError('Invalid or duplicate hop number')
        replies = hop.get('result', [])
        if not isinstance(replies, list) or len(replies) > 32:
            raise ValueError('Traceroute reply budget exceeded')
        addresses = {};timeouts = 0;errors = [];other = 0
        for reply in replies:
            if not isinstance(reply, dict):
                raise ValueError('Invalid traceroute reply')
            if 'from' in reply:
                address = str(ip_address(reply['from']))
                if ip_address(address).version != metadata['af']:
                    raise ValueError('Reply address family mismatch')
                value = finite_number(reply['rtt']) if 'rtt' in reply else None
                entry = addresses.setdefault(address, {'address': address, 'rtts_ms': [], 'replies': 0})
                entry['replies'] += 1
                if value is not None:entry['rtts_ms'].append(value)
                destination_replied |= address == target
            elif reply.get('x') == '*':timeouts += 1
            else:other += 1
            if 'err' in reply or 'error' in reply:
                errors.append(reply.get('err', reply.get('error')))
        responders = []
        for entry in addresses.values():
            values = entry['rtts_ms']
            entry.update(median_rtt_ms=statistics.median(values) if values else None,
                         min_rtt_ms=min(values) if values else None, max_rtt_ms=max(values) if values else None)
            responders.append(entry)
        by_ttl[ttl] = {'ttl': ttl, 'state': 'replies' if responders else 'no_reply' if timeouts else 'no_observation',
                       'responders': responders, 'timeouts': timeouts, 'errors': errors, 'other_replies': other,
                       'raw': hop}
    # Missing TTLs remain visible as missing observations, not imaginary routers.
    ordered = [by_ttl.get(ttl, {'ttl': ttl, 'state': 'not_recorded', 'responders': [], 'timeouts': None,
                              'errors': [], 'other_replies': 0}) for ttl in range(1, max(by_ttl, default=0) + 1)]
    return {'measurement_id': raw['msm_id'], 'probe_id': raw['prb_id'], 'observed_at': at.isoformat(),
            'family': raw['af'], 'destination': target, 'source_address': raw.get('src_addr', raw.get('from')),
            'protocol': raw.get('proto'), 'destination_replied': destination_replied,
            'measurement_error': raw.get('error'), 'hops': ordered, 'raw': raw}


def compare_traces(current, previous):
    if not previous:
        return {'state': 'no_prior_observation', 'changed_ttls': [], 'unknown_ttls': []}
    a = {h['ttl']: h for h in current['hops']};b = {h['ttl']: h for h in previous['hops']}
    changed = [];unknown = [];compared = 0
    for ttl in sorted(a.keys() | b.keys()):
        x, y = a.get(ttl), b.get(ttl)
        if not x or not y or not x['responders'] or not y['responders']:
            unknown.append(ttl);continue
        compared += 1
        if {r['address'] for r in x['responders']} != {r['address'] for r in y['responders']}:
            changed.append(ttl)
    state = 'responders_differ' if changed else 'incomplete_comparison' if unknown or not compared else 'observed_responders_match'
    return {'state': state, 'changed_ttls': changed, 'unknown_ttls': unknown, 'comparable_ttls': compared,
            'scope': 'Reply-address samples at these TTLs; ECMP, ICMP policy and reverse paths can differ. Not proof of a changed forwarding route.'}


def ping_observation(raw, metadata, probe, lower, through):
    if (type(raw.get('msm_id')) is not int or raw['msm_id'] != metadata['id'] or
            type(raw.get('prb_id')) is not int or raw['prb_id'] != probe['id'] or
            raw.get('type') != 'ping' or raw.get('af') != metadata['af']):
        raise ValueError('Ping identity mismatch')
    at=datetime.fromtimestamp(finite_number(raw.get('timestamp')),UTC)
    if not lower<=at<=through:raise ValueError('Ping outside requested window')
    target=str(ip_address(metadata['target_ip']))
    if str(ip_address(raw['dst_addr']))!=target or ip_address(target).version!=metadata['af']:
        raise ValueError('Ping destination mismatch')
    sent,received=raw.get('sent'),raw.get('rcvd')
    if type(sent) is not int or type(received) is not int or sent<1 or not 0<=received<=sent:
        if raw.get('error'):
            return {'measurement_id':raw['msm_id'],'probe_id':raw['prb_id'],'observed_at':at.isoformat(),
                    'family':raw['af'],'destination':target,'measurement_error':raw['error'],'sent':None,'received':None,
                    'loss_percent':None,'average_rtt_ms':None,'raw':raw}
        raise ValueError('Invalid ping packet counters')
    avg=finite_number(raw['avg']) if received and raw.get('avg') is not None else None
    return {'measurement_id':raw['msm_id'],'probe_id':raw['prb_id'],'observed_at':at.isoformat(),
            'family':raw['af'],'destination':target,'measurement_error':raw.get('error'),'sent':sent,'received':received,
            'loss_percent':100*(sent-received)/sent,'average_rtt_ms':avg,'raw':raw}
