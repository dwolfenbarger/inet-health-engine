"""Isolate source-trigger overhead using committed batches, no derived maintenance.

Identical source updates per arm, same logged tables/indexes. The trigger is only
turned off inside a transaction in the dedicated lifecycle QA database. Rollback
restores it even on failure. LSN deltas include any concurrent cluster activity.
"""
import asyncio,json,socket,sys,time,uuid
from pathlib import Path
sys.path.insert(0,'/opt/inet-journal-runtime/vendor')
import asyncpg
async def main():
 assert socket.gethostname()=='inet-health-dev1'
 c=await asyncpg.connect(database='inet_catalogue_capture_qa',user='postgres',host='/var/run/postgresql',command_timeout=15)
 assert await c.fetchval('SELECT current_database()')=='inet_catalogue_capture_qa'
 assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state<>'idle')"),'Run after other qualification completes'
 ROOT=Path(__file__).resolve().parents[1]
 await c.execute((ROOT/'docker/migrations/20261023-routing-state.sql').read_text())
 await c.execute((ROOT/'docker/migrations/20261027-catalogue-dirty.sql').read_text())
 await c.execute('TRUNCATE routing_routes,routing_observers CASCADE')
 g=uuid.UUID(int=55)
 await c.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation) VALUES('http-0','fixture','capture','192.0.2.1',64500,'mrt','{}',$1)",g)
 await c.execute("INSERT INTO routing_routes SELECT 'http-0',set_masklen('198.19.0.0'::inet+n,32)::cidr,1,$1,now(),2,'qa',true,64502,'[64500,64502]','{}','capture-fixture' FROM generate_series(0,9999) n",g)
 results=[]
 for name in ['disabled','enabled','disabled_repeat','enabled_repeat']:
  tx=c.transaction();await tx.start()
  try:
   if name.startswith('disabled'):await c.execute('ALTER TABLE routing_routes DISABLE TRIGGER inet_catalogue_route_dirty')
   await c.execute('TRUNCATE inet_incremental.dirty')
   await c.execute("SET LOCAL lock_timeout='500ms'; SET LOCAL statement_timeout='10s'")
   wal=await c.fetchval('SELECT pg_current_wal_insert_lsn()::text');started=time.monotonic();updates=0
   for i in range(20):
    result=await c.execute("UPDATE routing_routes SET event_at=event_at+interval '1 second' WHERE observer='http-0' AND prefix>=set_masklen('198.19.0.0'::inet+$1::int,32)::cidr AND prefix<set_masklen('198.19.0.0'::inet+($1::int+50),32)::cidr",i*50)
    updates+=int(result.split()[-1])
   results.append({'arm':name,'seconds':round(time.monotonic()-started,4),'route_updates':updates,'queued':await c.fetchval('SELECT count(*) FROM inet_incremental.dirty'),'cluster_wal_bytes':int(await c.fetchval('SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),$1::text::pg_lsn)',wal))})
  finally:await tx.rollback()
 assert await c.fetchval("SELECT tgenabled::text FROM pg_trigger WHERE tgname='inet_catalogue_route_dirty'")=='O'
 assert all(r['route_updates']==1000 and r['queued']==(0 if r['arm'].startswith('disabled') else 1000) for r in results)
 report={'pass':True,'results':results,'scope':'synthetic DEV; per-arm rollback, unique dirty keys, no production IOPS savings claim; LSN is cluster-wide'}
 Path('/tmp/inet-catalogue-capture.json').write_text(json.dumps(report,indent=2));print('PASS_CAPTURE',json.dumps(report),flush=True)
 await c.close()
asyncio.run(main())
