"""Bounded, read-only recovery diagnostics; never prints credentials."""
import asyncio,json,os
from pathlib import Path
for line in Path('/home/david/inet-health-engine/.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
async def main():
 import asyncpg
 c=await asyncpg.connect(os.environ['TIMESCALE_DSN'],command_timeout=8)
 for label,query in [
  ('ACTIVE_DATABASE_WORK',"SELECT pid,state,wait_event_type,wait_event,now()-query_start AS elapsed,left(query,100) AS statement FROM pg_stat_activity WHERE datname=current_database() AND state='active' AND pid<>pg_backend_pid()"),
  ('OPEN_GAPS',"SELECT observer,state,from_at,until_at,error_code FROM routing_gaps WHERE state NOT IN ('repaired','snapshot_superseded') ORDER BY opened_at LIMIT 20"),
  ('ROUTE_STATS',"SELECT n_live_tup,n_dead_tup,last_analyze,last_autoanalyze FROM pg_stat_user_tables WHERE relname='routing_routes'"),
  ('BASELINES',"SELECT observer,start_at,records,bytes,audit->>'snapshot_absences' AS absences FROM routing_archives WHERE kind='rib' ORDER BY observer")]:
  print(label,json.dumps([dict(r) for r in await c.fetch(query)],default=str),flush=True)
 row=await c.fetchrow("SELECT epoch,generation FROM routing_observers WHERE id='ris-rrc15-as28624-v4'")
 from datetime import datetime,timezone
 plan=await c.fetch("EXPLAIN SELECT prefix FROM routing_routes WHERE observer=$1 AND epoch=$2 AND generation=$3 AND present AND event_at<$4",'ris-rrc15-as28624-v4',row['epoch'],row['generation'],datetime(2026,10,23,8,tzinfo=timezone.utc))
 print('FINALIZATION_PLAN',[r[0] for r in plan],flush=True)
 sql=Path('/opt/inet-routing-canary/collectors/collectors/routing_store.py').read_text().split("result=await conn.execute('''",1)[1].split("'''",1)[0]
 plan=await c.fetch('EXPLAIN '+sql,'ris-rrc15-as28624-v4',row['epoch'],row['generation'],datetime(2026,10,23,8,tzinfo=timezone.utc),'https://data.ris.ripe.net/diagnostic-only')
 print('ACTUAL_UPDATE_PLAN',[r[0] for r in plan],flush=True)
 await c.execute('SET jit=off; SET enable_seqscan=off; SET enable_bitmapscan=off')
 try:
  actual=await c.fetch("EXPLAIN (ANALYZE,BUFFERS,TIMING OFF) SELECT prefix FROM routing_routes WHERE observer=$1 AND epoch=$2 AND generation=$3 AND present AND event_at<$4 LIMIT 1",'ris-rrc15-as28624-v4',row['epoch'],row['generation'],datetime(2026,10,23,8,tzinfo=timezone.utc))
  print('BOUNDED_FINALIZATION_READ',[r[0] for r in actual],flush=True)
 except Exception as exc:print('BOUNDED_FINALIZATION_READ',type(exc).__name__,flush=True)
 await c.close()
asyncio.run(main())
