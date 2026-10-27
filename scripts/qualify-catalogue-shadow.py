"""Actual service-user install refusal, detach, source progress and reset recovery."""
import asyncio,json,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(ROOT/'collectors')]
import asyncpg
from collectors.catalogue_incremental import maintain,reconcile_reset,finish_catchup,CURRENT,FIELDS
async def main():
 assert socket.gethostname()=='inet-health-dev1'
 c=await asyncpg.connect(database='inet_catalogue_lifecycle_qa',user='postgres',host='/var/run/postgresql',command_timeout=10)
 block=await asyncpg.connect(database='inet_catalogue_lifecycle_qa',user='postgres',host='/var/run/postgresql',command_timeout=3)
 def action(name):return subprocess.run([sys.executable,str(ROOT/'scripts/catalogue_shadow.py'),name],capture_output=True,text=True,timeout=8)
 old=await c.fetchval('SELECT max(id) FROM inet_incremental.publications WHERE ready');assert old
 before=await c.fetchval('SELECT count(*) FROM inet_incremental.routes_at($1)',old)
 # A busy source refuses DDL within the lock budget and leaves capture intact.
 await block.execute('BEGIN; LOCK TABLE routing_routes IN ROW EXCLUSIVE MODE')
 at=time.monotonic();refusal=action('install');elapsed=time.monotonic()-at
 await block.execute('ROLLBACK')
 assert refusal.returncode!=0 and ('lock timeout' in refusal.stderr or 'LockNotAvailable' in refusal.stderr),(refusal.stdout,refusal.stderr)
 assert elapsed<3
 assert await c.fetchval("SELECT tgenabled::text FROM pg_trigger WHERE tgname='inet_catalogue_route_dirty'")=='O'
 detached=action('detach');assert detached.returncode==0,(detached.stdout,detached.stderr)
 n=await c.fetchval('SELECT count(*) FROM inet_incremental.dirty')
 async with c.transaction():
  await c.execute('SET LOCAL ROLE inetuser')
  await c.execute("UPDATE routing_routes SET origin_asn=64509,path='[64500,64509]',event_at=event_at+interval '1 second' WHERE observer='http-0' AND prefix='198.18.0.2/32'")
 assert await c.fetchval('SELECT count(*) FROM inet_incremental.dirty')==n,'Capture was not detached'
 assert not await finish_catchup(c),'Reset barrier must prevent early publication'
 installed=action('install');assert installed.returncode==0,(installed.stdout,installed.stderr)
 while await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.resets)'):await reconcile_reset(c,64)
 while await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty)'):await maintain(c,64)
 assert await finish_catchup(c)
 assert not await c.fetchval('SELECT EXISTS((SELECT '+FIELDS+' FROM inet_incremental.current_routes EXCEPT '+CURRENT+') UNION ALL ('+CURRENT+' EXCEPT SELECT '+FIELDS+' FROM inet_incremental.current_routes))')
 assert await c.fetchval('SELECT count(*) FROM inet_incremental.routes_at($1)',old)==before
 report={'pass':True,'ddl_lock_refusal_seconds':round(elapsed,3),'source_progress_while_detached':True,'full_reset_reconciles_capture_gap':True,'old_pinned_view_preserved':True,'role':'postgres actual process plus inetuser source mutation'}
 Path('/tmp/inet-catalogue-shadow-qualification.json').write_text(json.dumps(report,indent=2));print('PASS_SHADOW_LIFECYCLE',json.dumps(report),flush=True)
 await block.close();await c.close()
asyncio.run(main())
