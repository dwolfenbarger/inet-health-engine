"""Persistent synthetic shadow fixture for repeatable index/pagination benchmarks.

Never touches source tables. Only the dedicated catalogue QA database is allowed.
"""
import asyncio,json,socket,sys,time,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(ROOT/'collectors'),str(ROOT/'api')]
import asyncpg
from collectors.catalogue_incremental import install,begin_version
from api.catalogue_backend import ScopedPool,OFFSET
async def main():
 assert socket.gethostname()=='inet-health-dev1'
 c=await asyncpg.connect(database='inet_catalogue_qa',user='postgres',host='/var/run/postgresql',command_timeout=300)
 assert await c.fetchval('SELECT current_database()')=='inet_catalogue_qa'
 await c.execute("SET lock_timeout='2s'")
 await install(c)
 await c.execute("SET statement_timeout='300s'; SET lock_timeout='2s'; SET work_mem='32MB'; SET temp_file_limit='4GB'; SET jit=off")
 size=int(os.environ.get('INET_SCALE_ROUTES','1000000'));assert 40000<=size<=4000000
 if await c.fetchval('SELECT count(*) FROM inet_incremental.current_routes')!=size:
  async with c.transaction():
   await c.execute('TRUNCATE inet_incremental.current_routes,inet_incremental.current_memberships,inet_incremental.current_routes_history,inet_incremental.current_memberships_history,inet_incremental.asns,inet_incremental.asns_history,inet_incremental.publications')
   pinned=await begin_version(c)
   await c.execute("""INSERT INTO inet_incremental.current_routes(observer,prefix,origin_asn,peer_asn,collector,transit,members,family,event_at,path,attrs,evidence_id,rank)
    SELECT 'read-scale',set_masklen('198.18.0.0'::inet+n,32)::cidr,64502,64500,'fixture',ARRAY[64500,64501]::bigint[],ARRAY[64500,64501,64502]::bigint[],4,now(),'[64500,64501,64502]','{}','synthetic-read',2 FROM generate_series(0,$1::int-1) n""",size)
   print('READ_ROUTE_FIXTURE',size,flush=True)
   await c.execute('INSERT INTO inet_incremental.current_memberships(asn,observer,prefix,roles) SELECT 64500,observer,prefix,6 FROM inet_incremental.current_routes')
   print('READ_MEMBERSHIP_FIXTURE',size,flush=True)
   await begin_version(c)
   await c.execute("UPDATE inet_incremental.current_routes SET event_at=event_at+interval '1 second' WHERE prefix<'198.18.8.0/32'::cidr")
   await c.execute("UPDATE inet_incremental.current_memberships SET roles=4 WHERE prefix<'198.18.8.0/32'::cidr")
  print('READ_FIXTURE_COMMITTED',size,flush=True)
 else:pinned=await c.fetchval('SELECT min(id) FROM inet_incremental.publications')
 for table in ['current_routes','current_memberships','current_routes_history','current_memberships_history']:
  await c.execute('ANALYZE inet_incremental.'+table)
 scoped=ScopedPool(c,OFFSET+pinned)
 queries={
 'direct_control':'SELECT observer,prefix FROM inet_incremental.current_routes WHERE _from<=($1-1000000000000) ORDER BY observer,prefix LIMIT 100',
 'global_first_page':'SELECT observer,prefix FROM inet_catalog.routes WHERE generation=$1 ORDER BY observer,prefix LIMIT 100',
 'origin_first_page':'SELECT observer,prefix FROM inet_catalog.routes WHERE generation=$1 AND origin_asn=64502 ORDER BY observer,prefix LIMIT 100',
 'asn_first_page':'SELECT observer,prefix FROM inet_catalog.memberships WHERE generation=$1 AND asn=64500 ORDER BY observer,prefix LIMIT 100',
 'global_next_page':"SELECT observer,prefix FROM inet_catalog.routes WHERE generation=$1 AND (observer,prefix)>('read-scale','198.18.0.99/32'::cidr) ORDER BY observer,prefix LIMIT 100",
 'asn_page_join':"WITH page AS MATERIALIZED (SELECT observer,prefix FROM inet_catalog.memberships WHERE generation=$1 AND asn=64500 ORDER BY observer,prefix LIMIT 100) SELECT r.observer,r.prefix FROM page p JOIN inet_catalog.routes r ON r.generation=$1 AND r.observer=p.observer AND r.prefix=p.prefix ORDER BY r.observer,r.prefix"
 }
 report={'routes':size,'pinned':pinned,'checks':{}}
 for name,sql in queries.items():
  values=[OFFSET+pinned]
  if name=='direct_control':raw=sql
  elif name=='asn_page_join':raw,values=scoped.member_route_query(['generation=$1','asn=64500'],values,100)
  elif name=='asn_first_page':raw,values=scoped.page_query('memberships',['generation=$1','asn=64500'],values,100)
  else:
   clauses=['generation=$1']
   if name=='origin_first_page':clauses.append('origin_asn=64502')
   if name=='global_next_page':clauses.append("(observer,prefix)>('read-scale','198.18.0.99/32'::cidr)")
   raw,values=scoped.page_query('routes',clauses,values,100)
  plan=json.loads(await c.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+raw,*values))[0]
  rows=await c.fetch(raw,*values)
  assert len(rows)==100
  report['checks'][name]={'execution_ms':plan['Execution Time'],'shared_hit_blocks':plan['Plan'].get('Shared Hit Blocks',0),'shared_read_blocks':plan['Plan'].get('Shared Read Blocks',0),'plan':plan['Plan']}
  print('READ_CHECK',name,plan['Execution Time'],flush=True)
  Path('/tmp/inet-catalogue-read-scale.json').write_text(json.dumps(report,indent=2))
  # Complete plans remain in the private JSON artifact, not verbose journal lines.
 Path('/tmp/inet-catalogue-read-scale.json').write_text(json.dumps(report,indent=2))
 assert all(v['execution_ms']<200 for v in report['checks'].values()),'Read gate200ms'
 print('PASS_READ_SCALE',json.dumps({'routes':size,'checks':{k:{x:y for x,y in v.items() if x!='plan'} for k,v in report['checks'].items()}}),flush=True)
 await c.close()
asyncio.run(main())
