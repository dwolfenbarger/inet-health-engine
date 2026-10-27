"""Lossless observer identity and deterministic single-path route state.

ADD_PATH is refused by archive audit until its path identifier is represented.
A changed origin or an absent route never implies a confirmed outage/hijack.
"""
from datetime import datetime,timezone
from ipaddress import ip_address,ip_network
import json,re

UTC=timezone.utc

def stamp(value):
    if isinstance(value,datetime):return value.astimezone(UTC)
    if isinstance(value,(int,float)):return datetime.fromtimestamp(value,UTC)
    return datetime.fromisoformat(value.replace('Z','+00:00')).replace(tzinfo=UTC) if not re.search(r'(Z|[+-]\d\d:\d\d)$',value) else datetime.fromisoformat(value.replace('Z','+00:00'))

def asn(value):
    n=int(value)
    if not 1<=n<=4294967295:raise ValueError('ASN out of range')
    return n

def path_asn(value):
    n=int(value)
    if not 0<=n<=4294967295:raise ValueError('Path ASN out of range')
    return n

def segments(value):
    if isinstance(value,list):
        out=[]
        for part in value:
            out.append([path_asn(n) for n in part] if isinstance(part,list) else path_asn(part))
        return out
    if not value:return []
    # Preserve complex segments, including confederations, without inventing an AS sequence.
    tokens=re.findall(r'\{[^}]*\}|\([^)]*\)|\[[^]]*\]|\d+',value)
    if re.sub(r'\s+','', ''.join(tokens))!=re.sub(r'\s+','',value):
        return {'unparsed':value}
    out=[]
    for token in tokens:
        if token.isdigit():out.append(path_asn(token))
        elif token.startswith('{'):out.append([path_asn(n) for n in re.split(r'[,\s]+',token[1:-1].strip()) if n])
        else:out.append({'confederation':token})
    return out

def origin(path):
    return path[-1] if isinstance(path,list) and path and isinstance(path[-1],int) and path[-1]>0 else None

def ris_changes(raw,observer):
    data=raw['data']
    if data['host'].split('.')[0]!=observer['collector'] or str(ip_address(data['peer']))!=str(ip_address(observer['peer_ip'])):
        raise ValueError('Observer identity mismatch')
    if asn(data['peer_asn'])!=observer['peer_asn']:raise ValueError('Peer ASN changed')
    at=stamp(data['timestamp']);message_id=str(data['id'])
    if data['type']!='UPDATE':return at,message_id,[]
    path=segments(data.get('path',[]));result=[]
    for present,entries in [(False,[{'prefixes':data.get('withdrawals',[])}]),(True,data.get('announcements',[]))]:
        for entry in entries:
            for prefix in entry.get('prefixes',[]):
                result.append({'prefix':str(ip_network(prefix,strict=True)),'event_at':at,
                    'present':present,'origin_asn':origin(path) if present else None,'path':path if present else [],
                    'attrs':{'message_id':message_id,'next_hop':entry.get('next_hop'),
                             'community':data.get('community',[]),'raw_path':data.get('path',[])},
                    'evidence_id':message_id})
    return at,message_id,result

def mrt_change(element,observer,*,snapshot=None,order=0,url=''):
    data=json.loads(str(element)) if not isinstance(element,dict) else element
    if str(ip_address(data['peer_ip']))!=str(ip_address(observer['peer_ip'])) or asn(data['peer_asn'])!=observer['peer_asn']:
        raise ValueError('MRT observer identity mismatch')
    if data['elem_type'] not in ['A','W']:raise ValueError('Unexpected MRT element')
    path=segments(data.get('as_path'));present=data['elem_type']=='A'
    return {'prefix':str(ip_network(data['prefix'],strict=True)),
        'event_at':snapshot or stamp(data['timestamp']),'present':present,
        'origin_asn':origin(path) if present else None,'path':path if present else [],
        'attrs':data,'evidence_id':f'{url}#{order}'}

# State updates are ordered by provider time, then live/archive/snapshot rank,
# then persistent receive order. Archived replay cannot resurrect a live withdrawal.
def newer(incoming,current):
    return (incoming['event_at'],incoming['rank'],incoming['event_order'])>(current['event_at'],current['rank'],current['event_order'])
