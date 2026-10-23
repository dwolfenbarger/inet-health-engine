import asyncio,json,os,shutil
from pathlib import Path
root=Path('/home/david/inet-health-engine')
for line in (root/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
async def main():
 import asyncpg
 c=await asyncpg.connect(os.environ['TIMESCALE_DSN']);await c.execute("SET statement_timeout='8s'")
 rows=await c.fetch("SELECT id,mode,baseline_state,snapshot_v4,snapshot_v6,event_at,received_at,messages,updates,error_code FROM routing_observers ORDER BY id")
 print('OBSERVER_STATUS',json.dumps([dict(r) for r in rows],default=str),flush=True)
 for table in ['routing_routes','routing_messages','source_evidence','bgp_updates']:
  print('TABLE_BYTES',table,await c.fetchval('SELECT pg_total_relation_size($1::regclass)',table),flush=True)
 print('OBSERVER_TIME_INDEX_VALID',await c.fetchval("SELECT bool_and(i.indisvalid) FROM pg_index i JOIN pg_class x ON x.oid=i.indexrelid WHERE x.relname='routing_routes_observer_time'"),flush=True)
 print('ROOT_FREE_GIB',round(shutil.disk_usage('/').free/1024**3,2),flush=True)
 await c.close()
asyncio.run(main())
