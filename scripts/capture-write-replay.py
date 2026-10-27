"""Read-only, bounded routing sample for private development comparison."""
import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,'/opt/inet-journal-runtime/vendor')
import asyncpg
async def main():
 c=await asyncpg.connect(database='inethealth',user='postgres',host='/var/run/postgresql',command_timeout=15)
 async with c.transaction(readonly=True):
  observers=await c.fetch("SELECT id,collector,host(peer_ip) peer_ip,peer_asn FROM routing_observers WHERE mode='ris-live' ORDER BY id")
  out={'observers':[dict(r) for r in observers],'indexes':[dict(r) for r in await c.fetch("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='routing_routes'")],'frames':{}}
  for o in observers:
   rows=await c.fetch("SELECT payload,sequence FROM routing_messages WHERE observer=$1 AND stored_at>now()-interval '2 hours' ORDER BY received_at DESC LIMIT 512",o['id'])
   out['frames'][o['id']]=[dict(r) for r in sorted(rows,key=lambda r:r['sequence'],reverse=True)]
 Path('/tmp/inet-write-replay-input.json').write_text(json.dumps(out,default=str))
 print('CAPTURED',sum(len(r) for r in out['frames'].values()),'frames');await c.close()
asyncio.run(main())
