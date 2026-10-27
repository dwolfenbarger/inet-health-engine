"""Opt-in adapter preserving the existing catalogue API's generation contract."""
import json,os,re
from datetime import datetime,timezone
from fastapi import HTTPException
OFFSET=1_000_000_000_000
FIELDS='observer,prefix,origin_asn,peer_asn,collector,transit,members,family,event_at,path,attrs,evidence_id,rank'
COUNTS='asn,routes,prefixes,origins,transits,peers,ipv4,ipv6,observers'
def decoded(value):return json.loads(value) if isinstance(value,str) else value

def enabled():return os.environ.get('INET_INCREMENTAL_CATALOGUE','0').lower() in {'1','true'}

async def selected_generation(pool,requested=None):
    incremental=requested is not None and requested>=OFFSET
    if not enabled():
        if incremental:raise HTTPException(409,'Browsing snapshot expired; refresh to continue.')
        return None
    if requested is not None and not incremental:return None
    version=requested-OFFSET if incremental else None
    row=await pool.fetchrow('''SELECT * FROM inet_incremental.publications WHERE ready AND
      ($1::bigint IS NULL OR id=$1) ORDER BY id DESC LIMIT 1''',version)
    if row is None:
        if incremental:raise HTTPException(409,'Browsing snapshot expired; refresh to continue.')
        return None
    summary=decoded(row['summary']);observers=decoded(row['observers'])
    if not all(k in summary for k in ['routes','prefixes','origins','observers','asns','facets']):
        raise HTTPException(503,'Incremental inventory publication is incomplete.')
    status=await pool.fetchrow('''SELECT reset_barrier OR EXISTS(SELECT 1 FROM inet_incremental.resets) barrier,
      (SELECT min(queued_at) FROM inet_incremental.dirty) oldest FROM inet_incremental.runtime WHERE singleton''')
    now=datetime.now(timezone.utc);age=max(0,int((now-row['captured_at']).total_seconds()))
    lag=max(0,int((now-status['oldest']).total_seconds())) if status and status['oldest'] else 0
    barrier=not status or status['barrier']
    return {'id':OFFSET+row['id'],'captured_at':row['captured_at'],'published_at':row['captured_at'],
      'observers':observers,'summary':summary,'age_seconds':age,'stale':age>120 or lag>60 or barrier,
      'backend':'incremental','processing_lag_seconds':lag,'reset_pending':bool(barrier)}

async def retained(pool,generation):
    if generation<OFFSET:return None
    return bool(await pool.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.publications WHERE id=$1 AND ready)',generation-OFFSET))

class ScopedPool:
    def __init__(self,pool,generation):
        self.pool=pool;self.generation=int(generation);self.version=self.generation-OFFSET
        if self.version<1:raise ValueError('Invalid incremental generation')
    def query(self,sql):
        source={
          'routes':f'(SELECT {self.generation}::bigint generation,{FIELDS} FROM inet_incremental.routes_at({self.version}))',
          'memberships':f'(SELECT {self.generation}::bigint generation,asn,observer,prefix,roles FROM inet_incremental.memberships_at({self.version}))',
          'asns':f'(SELECT {self.generation}::bigint generation,{COUNTS} FROM inet_incremental.asns_at({self.version}) WHERE asn>0)'}
        return re.sub(r'\binet_catalog\.(routes|memberships|asns)\b',lambda m:source[m[1]],sql)
    async def fetch(self,sql,*args,**kwargs):return await self.pool.fetch(self.query(sql),*args,**kwargs)
    async def fetchrow(self,sql,*args,**kwargs):return await self.pool.fetchrow(self.query(sql),*args,**kwargs)
    async def fetchval(self,sql,*args,**kwargs):return await self.pool.fetchval(self.query(sql),*args,**kwargs)

def for_generation(pool,generation):
    return ScopedPool(pool,generation['id']) if generation.get('backend')=='incremental' else pool
