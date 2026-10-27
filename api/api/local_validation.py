"""Bounded on-demand origin validation for focused route investigations."""
from datetime import datetime,timedelta,timezone
from ipaddress import ip_network
async def validate(client,prefix,origin,now=None):
    now=now or datetime.now(timezone.utc)
    try:
        prefix=str(ip_network(prefix,strict=False));origin=int(origin)
        if not 1<=origin<=4294967295:return None
        response=await client.get(f'/api/v1/validity/AS{origin}/{prefix}')
        response.raise_for_status();body=response.json();answer=body['validated_route'];route=answer['route']
        if str(ip_network(route['prefix'],strict=False))!=prefix or int(str(route['origin_asn']).removeprefix('AS'))!=origin:return None
        at=datetime.fromisoformat(body['generatedTime'].replace('Z','+00:00'))
        if at.tzinfo is None or at>now+timedelta(minutes=2) or now-at>timedelta(minutes=60):return None
        state=answer['validity']['state']
        if state not in {'valid','invalid','not-found'}:return None
        return {'source':'rpki-local','prefix':prefix,'origin_asn':origin,'status':state,'validity':answer['validity'],
          'generated_at':at.isoformat(),'validation_completed_at':at.isoformat(),'checked_at':now.isoformat(),
          'freshness_limit_seconds':3600,'freshness_basis':'Routinator validity response; completed validation',
          'basis':'on-demand local origin validation; no AS-path safety or outage conclusion'}
    except Exception:return None
