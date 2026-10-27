"""Rollback-only point-join plans over the retained synthetic read fixture."""
import asyncio,json,sys,socket
from pathlib import Path
sys.path.insert(0,'/opt/inet-journal-runtime/vendor')
import asyncpg
async def main():
 assert socket.gethostname()=='inet-health-dev1'
 c=await asyncpg.connect(database='inet_catalogue_qa',user='postgres',host='/var/run/postgresql',command_timeout=30)
 tx=c.transaction();await tx.start()
 try:
  await c.execute("SET LOCAL jit=off; SET LOCAL work_mem='8MB'")
  await c.execute("CREATE TEMP TABLE point_keys AS SELECT observer,prefix FROM inet_incremental.current_routes WHERE observer='read-scale' AND prefix>='198.31.0.0/32'::cidr ORDER BY prefix LIMIT 64; ANALYZE point_keys")
  assert await c.fetchval('SELECT count(*) FROM point_keys')==64
  report={};states={}
  for name,key in [('equality','r.observer=k.observer AND r.prefix=k.prefix'),('scalar_bounds','r.observer=k.observer AND r.prefix>=k.prefix AND r.prefix<=k.prefix')]:
   sql='SELECT r.observer,r.prefix,r.path,r.attrs,r.event_at FROM point_keys k JOIN inet_incremental.current_routes r ON '+key
   plan=json.loads(await c.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql))[0]
   report[name]={'ms':plan['Execution Time'],'plan':plan['Plan']};states[name]=sorted(tuple(r) for r in await c.fetch(sql))
  assert states['equality']==states['scalar_bounds']
  Path('/tmp/inet-catalogue-point-plans.json').write_text(json.dumps(report,indent=2,default=str));print('PASS_POINT_PLANS',json.dumps({k:v['ms'] for k,v in report.items()}),flush=True)
 finally:await tx.rollback();await c.close()
asyncio.run(main())
