"""Bounded incremental worker. Explicit installation is a separate release step.

Runs against an already installed shadow; never modifies the source schema or
silently reseeds it. A failed batch rolls back, leaving dirty work durable.
"""
import asyncio,json,os,shutil,sys,time
from pathlib import Path
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(Path(__file__).resolve().parents[1]/'collectors')]
import asyncpg
from collectors.catalogue_incremental import bootstrap,maintain,reconcile_reset,finish_catchup,prune_versions

async def status(c):
 row=await c.fetchrow('''SELECT initialized,reset_barrier,
 (SELECT count(*) FROM inet_incremental.dirty) queued,
 (SELECT extract(epoch FROM clock_timestamp()-min(queued_at)) FROM inet_incremental.dirty) oldest_seconds,
 (SELECT count(*) FROM inet_incremental.resets) resets,
 (SELECT max(captured_at) FROM inet_incremental.publications WHERE ready) published_at,
 (SELECT coalesce(sum(pg_total_relation_size(c.oid)),0) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='inet_incremental' AND c.relkind='r') bytes
 FROM inet_incremental.runtime WHERE singleton''')
 if row is None:raise RuntimeError('Incremental runtime is not installed')
 return dict(row)

async def run(c,*,seconds=60,batch=64,max_bytes=24*1024**3,reserve_bytes=35*1024**3,allow_bootstrap=False):
 if not 1<=seconds<=3600 or not 1<=batch<=256:raise ValueError('Worker budget')
 state=await status(c)
 if not state['initialized'] and not allow_bootstrap:raise RuntimeError('Explicit bootstrap required')
 if state['bytes']>=max_bytes or shutil.disk_usage('/').free<reserve_bytes:raise RuntimeError('Shadow storage admission budget; preserve source and published view')
 await c.execute("SET lock_timeout='200ms'; SET statement_timeout='2s'; SET idle_in_transaction_session_timeout='5s'; SET jit=off; SET work_mem='8MB'; SET temp_file_limit='256MB'")
 if not state['initialized']:
  raise RuntimeError('Bootstrap requires separate measured bulk window; bounded worker refuses it')
 started=time.monotonic();processed=0;changed=0;timeouts=0;published=0;max_batch=0;next_status=started;next_prune=started
 while time.monotonic()-started<seconds:
  now=time.monotonic()
  if now>=next_status:
   state=await status(c);next_status=now+10
   if state['bytes']>=max_bytes or shutil.disk_usage('/').free<reserve_bytes:
    raise RuntimeError('Shadow storage budget reached; source and queue preserved, no further publication')
  try:
   at=time.monotonic()
   if await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.resets)'):
    result=await reconcile_reset(c,batch)
   else:
    result=await maintain(c,batch);processed+=result.get('processed',0);changed+=result.get('changed',0)
    # Ready metadata is published only after catching up atomically with intake.
    if now-published>=5 and not await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty)'):
     # Publish at the just-drained batch boundary. Waiting for an additional
     # empty maintenance cycle can starve publication on a continuous feed.
     version=await finish_catchup(c)
     if version:published=now
   max_batch=max(max_batch,time.monotonic()-at)
   if now>=next_prune:await prune_versions(c,3600,1024);next_prune=now+1
   if not result.get('processed') and not result.get('queued'):await asyncio.sleep(.1)
  except (asyncpg.LockNotAvailableError,asyncpg.QueryCanceledError):
   timeouts+=1;await asyncio.sleep(.1)
 result={'processed':processed,'changed':changed,'timeouts':timeouts,'max_batch_seconds':round(max_batch,4),'elapsed_seconds':round(time.monotonic()-started,3),'status':await status(c)}
 print(json.dumps(result,default=str),flush=True);return result

async def main():
 import argparse,socket
 p=argparse.ArgumentParser();p.add_argument('--database',default='inet_catalogue_lifecycle_qa');p.add_argument('--seconds',type=int,default=60);p.add_argument('--batch',type=int,default=64);p.add_argument('--status',action='store_true');args=p.parse_args()
 # Production invocation is intentionally refused until the release installer and
 # full-scale shadow gates have been qualified. Avoid an accidental live trial.
 if socket.gethostname()!='inet-health-dev1' or args.database not in {'inet_catalogue_qa','inet_catalogue_lifecycle_qa'}:raise RuntimeError('DEV qualification only')
 c=await asyncpg.connect(database=args.database,user='postgres',host='/var/run/postgresql',command_timeout=5,server_settings={'application_name':'inet-incremental-qualification'})
 try:
  if args.status:print(json.dumps(await status(c),default=str),flush=True)
  else:await run(c,seconds=args.seconds,batch=args.batch)
 finally:await c.close()
if __name__=='__main__':asyncio.run(main())
