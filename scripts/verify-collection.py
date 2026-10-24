"""Run one bounded real collector from candidate source without Celery scheduling."""
import argparse,asyncio,json,os,sys,time
from pathlib import Path
ROOT=Path('/home/david/inet-health-engine');CANDIDATE=Path(__file__).resolve().parents[1]
for line in (ROOT/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
sys.path.insert(0,str(CANDIDATE/'collectors'))
async def main(mode):
 from collectors.db import close_pg_pool,get_pg_pool
 from collectors.cache import close_redis
 started=time.monotonic()
 try:
  if mode=='rpki':
   from collectors.rpki_collector import run_collection_cycle
   records=await asyncio.wait_for(run_collection_cycle(),timeout=240)
   print('VALIDATOR_RECORDS',len(records),'STATES',json.dumps({s:sum(r['payload']['status']==s for r in records) for s in ['valid','invalid','not-found']}),flush=True)
  elif mode=='atlas':
   from collectors.public_sources import run_atlas_cycle
   records=await asyncio.wait_for(run_atlas_cycle(),timeout=240);print('ATLAS_RECORDS',len(records),flush=True)
  else:
   from collectors.ripe_ris_collector import run_collection_cycle
   await asyncio.wait_for(run_collection_cycle(60),timeout=240)
  pool=await get_pg_pool()
  print('COLLECTOR_PROOF',json.dumps({'mode':mode,'elapsed_seconds':round(time.monotonic()-started,2),
   'sources':[dict(r) for r in await pool.fetch('SELECT source,state,records,last_attempt,last_success,last_observation,error_code FROM source_status WHERE source=ANY($1::text[])',['atlas','rpki-local'])]},default=str),flush=True)
 finally:await close_pg_pool();await close_redis()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['rpki','atlas','ris']);asyncio.run(main(p.parse_args().mode))
