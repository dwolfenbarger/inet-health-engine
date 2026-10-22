"""Covering-prefix origin validation against a locally verified VRP snapshot."""
from ipaddress import ip_network

class VRPIndex:
    def __init__(self,roas):
        self.tables={4:{},6:{}}
        self.count=0
        for roa in roas:
            net=ip_network(roa['prefix'],strict=True)
            asn=int(str(roa['asn']).removeprefix('AS'))
            maximum=int(roa['maxLength'])
            if not (0<=asn<=4294967295 and net.prefixlen<=maximum<=net.max_prefixlen):
                raise ValueError('Invalid VRP')
            table=self.tables[net.version].setdefault(net.prefixlen,{})
            table.setdefault(int(net.network_address),[]).append((asn,maximum,roa.get('ta')))
            self.count+=1
        if not self.count:raise ValueError('Empty validated ROA dataset')

    def validate(self,prefix,asn):
        net=ip_network(prefix,strict=True)
        if not 1<=asn<=4294967295:raise ValueError('Invalid origin ASN')
        address=int(net.network_address);bits=net.max_prefixlen
        matches=[];wrong_as=[];wrong_length=[]
        for length,table in self.tables[net.version].items():
            if length>net.prefixlen:continue
            covering=(address>>(bits-length))<<(bits-length) if length else 0
            for origin,maximum,ta in table.get(covering,[]):
                # Construct with an explicit address family, including IPv6 ::/0.
                from ipaddress import IPv4Network,IPv6Network
                cls=IPv4Network if net.version==4 else IPv6Network
                vrp={'asn':f'AS{origin}','prefix':str(cls((covering,length))),
                     'max_length':str(maximum),'ta':ta}
                if origin!=asn:wrong_as.append(vrp)
                elif net.prefixlen>maximum:wrong_length.append(vrp)
                else:matches.append(vrp)
        state='valid' if matches else 'invalid' if wrong_as or wrong_length else 'not-found'
        value={'state':state,'VRPs':{'matched':matches,'unmatched_as':wrong_as,'unmatched_length':wrong_length}}
        if state=='invalid':value['reason']='length' if wrong_length else 'as'
        return value
