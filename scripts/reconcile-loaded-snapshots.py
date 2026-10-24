"""Reconcile a completed audited UAT snapshot without overwriting newer live data."""
import asyncio,json,os,socket,sys
from pathlib import Path
if socket.gethostname()!='inet-health-uat':raise SystemExit('UAT only')
root=Path('/home/david/inet-health-engine')
for line in (root/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
sys.path.insert(0,'/tmp/inet-routing-candidate/collectors')
async def main():
 import asyncpg
 from collectors.routing_store import finalize_snapshot
 c=await asyncpg.connect(os.environ['TIMESCALE_DSN'])
 for ident in ['ris-rrc11-as2497-v4','ris-rrc21-as15547-v6','ris-rrc15-as28624-v4']:
  async with c.transaction():
   row=await c.fetchrow('SELECT * FROM routing_observers WHERE id=$1 FOR UPDATE',ident)
   if row is None or row['baseline_at'] is None:continue
   archive=await c.fetchrow("SELECT * FROM routing_archives WHERE observer=$1 AND generation=$2 AND kind='rib' AND start_at=$3",ident,row['generation'],row['baseline_at'])
   if not archive:raise RuntimeError('Completed audited snapshot proof missing')
   review=json.loads(archive['audit'])
   if review['rib_v4']!=row['snapshot_v4'] or review['rib_v6']!=row['snapshot_v6']:raise RuntimeError('Snapshot audit count mismatch')
   removed=await finalize_snapshot(c,ident,row['epoch'],row['generation'],row['baseline_at'],archive['url'])
   review['snapshot_absences']=review.get('snapshot_absences',0)+removed
   await c.execute('UPDATE routing_archives SET audit=$4::jsonb WHERE observer=$1 AND generation=$2 AND url=$3',ident,row['generation'],archive['url'],json.dumps(review))
   print('SNAPSHOT_ABSENCE_RECONCILED',ident,removed,flush=True)
 assert await c.fetchval('SELECT count(*) FROM routing_gaps WHERE from_at>until_at')==0
 await c.close()
asyncio.run(main())
