"""Audit complete compressed MRT files, selected peer counts and session changes.

RIB element counts must match the independent peer-index/RIB-entry walk. Archive
session changes are retained as continuity risks, never silently ignored by an
UPDATE-only decoder. Unsupported selected ADD_PATH refuses reconstruction.
"""
import bz2,gzip,struct
from ipaddress import ip_address

class AuditError(ValueError):pass

def read_exact(file,n):
    data=file.read(n)
    if len(data)!=n:raise AuditError('Truncated MRT record')
    return data

def audit(path,peer_ip):
    opener=gzip.open if str(path).endswith('.gz') else bz2.open
    target=str(ip_address(peer_ip));peers={};counts={4:0,6:0};states=[];records=0;selected_messages=0
    found=False
    with opener(path,'rb') as stream:
        while True:
            header=stream.read(12)
            if not header:break
            if len(header)!=12:raise AuditError('Truncated MRT header')
            ts,kind,subtype,size=struct.unpack('!IHHI',header)
            if size>8*1024**2:raise AuditError('MRT record exceeds bounded audit size')
            payload=read_exact(stream,size);records+=1
            if kind==13 and subtype==1:
                if len(payload)<8:raise AuditError('Invalid peer index')
                name_len=struct.unpack_from('!H',payload,4)[0];offset=6+name_len
                number=struct.unpack_from('!H',payload,offset)[0];offset+=2
                for index in range(number):
                    flags=payload[offset];offset+=5
                    width=16 if flags&1 else 4
                    address=str(ip_address(payload[offset:offset+width]));offset+=width
                    as_width=4 if flags&2 else 2
                    peer_asn=int.from_bytes(payload[offset:offset+as_width],'big');offset+=as_width
                    peers[index]=(address,peer_asn)
                    if address==target:found=True
                if offset!=len(payload):raise AuditError('Invalid peer index length')
            elif kind==13 and subtype in [2,4,8,10]:
                family=4 if subtype in [2,8] else 6
                plen=payload[4];offset=5+(plen+7)//8
                if plen>(32 if family==4 else 128):raise AuditError('Invalid RIB prefix')
                entries=struct.unpack_from('!H',payload,offset)[0];offset+=2
                for _ in range(entries):
                    index=struct.unpack_from('!H',payload,offset)[0];offset+=6
                    if subtype in [8,10]:offset+=4
                    length=struct.unpack_from('!H',payload,offset)[0];offset+=2
                    if index not in peers:raise AuditError('Unknown MRT peer index')
                    if peers[index][0]==target:
                        if subtype in [8,10]:raise AuditError('Selected ADD_PATH needs path identifier support')
                        counts[family]+=1
                    offset+=length
                if offset!=len(payload):raise AuditError('Invalid RIB entry length')
            elif kind in [16,17]:
                body=payload[4:] if kind==17 else payload
                micro=struct.unpack_from('!I',payload)[0]/1e6 if kind==17 else 0
                if subtype not in [0,1,4,5,6,7,8,9,10,11]:
                    raise AuditError('Unsupported BGP4MP record subtype')
                width=4 if subtype in [4,5,7,9,11] else 2
                afi=struct.unpack_from('!H',body,2*width+2)[0]
                if afi not in [1,2]:raise AuditError('Unsupported peer transport family')
                addr_width=4 if afi==1 else 16;offset=2*width+4
                address=str(ip_address(body[offset:offset+addr_width]));offset+=2*addr_width
                if address==target:
                    found=True
                    if subtype in [8,9,10,11]:raise AuditError('Selected ADD_PATH needs path identifier support')
                    if subtype in [0,5]:
                        old,new=struct.unpack_from('!HH',body,offset)
                        states.append({'time':ts+micro,'old':old,'new':new})
                    else:selected_messages+=1
            elif kind==13 and subtype not in [3,5]:
                raise AuditError('Unsupported TABLE_DUMP_V2 subtype')
            elif kind not in [13]:
                raise AuditError('Unsupported MRT record type')
    return {'records':records,'selected_peer_present':found,'rib_v4':counts[4],'rib_v6':counts[6],
            'state_changes':states,'selected_messages':selected_messages}
