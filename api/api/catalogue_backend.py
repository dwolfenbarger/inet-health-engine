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
        # General snapshot relations support aggregates and selective lookups.
        # Ordered pages use the explicitly bounded methods below instead.
        source={}
        for name,table,columns,extra in [
          ('routes','current_routes',FIELDS,''),
          ('memberships','current_memberships','asn,observer,prefix,roles',''),
          ('asns','asns',COUNTS,' AND asn>0')]:
            select=f'SELECT {self.generation}::bigint generation,{columns} FROM inet_incremental.'
            source[name]=f'({select}{table} WHERE _from<={self.version}{extra} UNION ALL {select}{table}_history WHERE _from<={self.version} AND _until>{self.version}{extra})'
        return re.sub(r'\binet_catalog\.(routes|memberships|asns)\b',lambda m:source[m[1]],sql)
    async def fetch(self,sql,*args,**kwargs):return await self.pool.fetch(self.query(sql),*args,**kwargs)
    async def fetchrow(self,sql,*args,**kwargs):return await self.pool.fetchrow(self.query(sql),*args,**kwargs)
    async def fetchval(self,sql,*args,**kwargs):return await self.pool.fetchval(self.query(sql),*args,**kwargs)

    def relation(self,table,history=False,alias='r'):
        name={'routes':'current_routes','memberships':'current_memberships','asns':'asns'}[table]
        fields={'routes':FIELDS,'memberships':'asn,observer,prefix,roles','asns':COUNTS}[table]
        columns=','.join(f'{alias}.{x}' for x in fields.split(','))
        if table=='routes':columns+=f',{alias}.prefix::text prefix_text'
        projection=f'{self.generation}::bigint generation,{columns}'
        condition=f'{alias}._from<={self.version}'
        if history:condition+=f' AND {alias}._until>{self.version}';name+='_history'
        if table=='asns':condition+=f' AND {alias}.asn>0'
        return projection,f'inet_incremental.{name} {alias}',condition

    def page_query(self,table,clauses,args,limit):
        if not 1<=limit<=201:raise ValueError('Page budget')
        where=' AND '.join(clauses) or 'true'
        where=re.sub(r'\b(?:r\.)?generation\s*=\s*\$1',f'{self.generation}::bigint=$1',where)
        order='asn' if table=='asns' else 'observer,prefix'
        branches=[]
        for history in [False,True]:
            projection,source,version=self.relation(table,history)
            branches.append(f'(SELECT {projection} FROM {source} WHERE {version} AND ({where}) ORDER BY '+','.join('r.'+x for x in order.split(','))+f' LIMIT ${len(args)+1})')
        # Bound each indexed branch before merging. PostgreSQL can otherwise
        # sort every current/history row for a high-cardinality ASN or global page.
        return 'SELECT * FROM ('+' UNION ALL '.join(branches)+f') bounded ORDER BY {order} LIMIT ${len(args)+1}',[*args,limit]

    async def page_fetch(self,table,clauses,args,limit):
        sql,values=self.page_query(table,clauses,args,limit)
        return await self.pool.fetch(sql,*values)

    def member_route_query(self,clauses,args,limit):
        page,values=self.page_query('memberships',clauses,args,limit)
        branches=[]
        for history in [False,True]:
            projection,source,version=self.relation('routes',history)
            # Equal scalar bounds select the (observer,prefix) btree. A plain
            # prefix equality can choose the much costlier network GiST for each
            # member even when the complete primary key is available.
            key='r.observer=p.observer AND r.prefix>=p.prefix AND r.prefix<=p.prefix'
            branches.append(f'SELECT {projection} FROM {source} WHERE {version} AND {key}')
        # OFFSET 0 retains a bounded parameterized key lookup per page member,
        # rather than letting a hash join scan the entire routing inventory.
        sql='WITH page AS MATERIALIZED ('+page+') SELECT found.* FROM page p CROSS JOIN LATERAL ('+' UNION ALL '.join(branches)+' OFFSET 0) found ORDER BY found.observer,found.prefix'
        return sql,values

    async def member_routes(self,clauses,args,limit):
        sql,values=self.member_route_query(clauses,args,limit)
        return await self.pool.fetch(sql,*values)

def for_generation(pool,generation):
    return ScopedPool(pool,generation['id']) if generation.get('backend')=='incremental' else pool
