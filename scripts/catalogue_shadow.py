"""Explicit shadow-only installation/bootstrap; public API flag remains untouched.

Rollback removes only owned capture triggers, preserving source and all journal
rows. Derived evidence is kept for diagnosis; a later resume requires full reset reconciliation.
"""
import asyncio,argparse,json,os,shutil,socket,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(ROOT/'collectors'),str(ROOT/'scripts')]
import asyncpg
from collectors.catalogue_incremental import install,bootstrap
from catalogue_runtime import status,run
MAX=24*1024**3;RESERVE=35*1024**3
async def budget(c):
 size=await c.fetchval("SELECT coalesce(sum(pg_total_relation_size(x.oid)),0) FROM pg_class x JOIN pg_namespace n ON n.oid=x.relnamespace WHERE n.nspname='inet_incremental' AND x.relkind='r'")
 free=shutil.disk_usage('/').free
 if size>MAX or free<RESERVE:raise RuntimeError('Shadow24GiB/root35GiB reserve exceeded')
 print('BUDGET',json.dumps({'bytes':int(size),'root_free':free}),flush=True)
async def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['install','bootstrap','run','status','detach']);p.add_argument('--database',default='inet_catalogue_lifecycle_qa');p.add_argument('--production-shadow',action='store_true');p.add_argument('--seconds',type=int,default=60);args=p.parse_args()
 production=args.production_shadow
 if production:
  if socket.gethostname()!='inet-health-prod' or args.database!='inethealth':raise RuntimeError('Production identity mismatch')
 else:
  if socket.gethostname()!='inet-health-dev1' or args.database!='inet_catalogue_lifecycle_qa':raise RuntimeError('Dedicated lifecycle QA only')
 c=await asyncpg.connect(database=args.database,user='postgres',host='/var/run/postgresql',command_timeout=900,server_settings={'application_name':'inet-catalogue-shadow'})
 try:
  if args.action=='install':
   await budget(c)
   estimate=await c.fetchval("SELECT pg_total_relation_size('routing_routes')*4")
   if production and (estimate>MAX or shutil.disk_usage('/').free<RESERVE+estimate):raise RuntimeError('Conservative bootstrap admission budget')
   # DDL source locks are bounded; every object and trigger commits together.
   async with c.transaction():
    await c.execute("SET LOCAL lock_timeout='200ms'; SET LOCAL statement_timeout='15s'")
    migration=(ROOT/'docker/migrations/20261027-catalogue-dirty.sql').read_text().replace('BEGIN;','').replace('COMMIT;','')
    await c.execute(migration);await install(c,production_shadow=production)
   print('INSTALLED_SHADOW_CAPTURE',flush=True)
  elif args.action=='bootstrap':
   await budget(c)
   await c.execute("SET statement_timeout='900s'; SET lock_timeout='200ms'; SET work_mem='32MB'; SET maintenance_work_mem='128MB'; SET temp_file_limit='4GB'; SET jit=off")
   at=time.monotonic();result=await bootstrap(c,check_budget=budget)
   print('BOOTSTRAP_COMMITTED',json.dumps(result|{'seconds':time.monotonic()-at}),flush=True)
  elif args.action=='run':await run(c,seconds=args.seconds,batch=64)
  elif args.action=='detach':
   # Explicit rollback path, never automatic source data deletion. Remove only
   # this feature's triggers and invalidate further incremental publication.
   async with c.transaction():
    await c.execute("SET LOCAL lock_timeout='200ms'; SET LOCAL statement_timeout='5s'")
    await c.execute('''DROP TRIGGER IF EXISTS inet_catalogue_route_dirty ON routing_routes;
    DROP TRIGGER IF EXISTS inet_catalogue_observer_reset ON routing_observers;
    DROP TRIGGER IF EXISTS inet_catalogue_source_truncate ON routing_routes;
    INSERT INTO inet_incremental.resets(observer) SELECT id FROM routing_observers WHERE true
    ON CONFLICT(observer) DO UPDATE SET revision=inet_incremental.resets.revision+1,queued_at=excluded.queued_at;
    UPDATE inet_incremental.runtime SET reset_barrier=true WHERE singleton''')
   print('CAPTURE_DETACHED_SOURCE_PRESERVED_FULL_RESET_REQUIRED',flush=True)
  else:print(json.dumps(await status(c),default=str),flush=True)
 finally:await c.close()
if __name__=='__main__':asyncio.run(main())
