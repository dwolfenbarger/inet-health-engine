"""Committed concurrent ingestion versus bounded catalogue worker in isolated DEV."""
import asyncio,json,sys,time,socket,random
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(ROOT/'collectors'),str(ROOT/'scripts')]
import asyncpg
from collectors.catalogue_incremental import maintain,finish_catchup,CURRENT,FIELDS
from catalogue_runtime import run
DB='inet_catalogue_lifecycle_qa'
async def connect():return await asyncpg.connect(database=DB,user='postgres',host='/var/run/postgresql',command_timeout=15)
async def main():
 assert socket.gethostname()=='inet-health-dev1'
 c=await connect();w=await connect();block=await connect()
 g=await c.fetchval("SELECT generation FROM routing_observers WHERE id='http-0'");assert g
 await c.execute("INSERT INTO routing_routes SELECT 'http-0',set_masklen('198.19.0.0'::inet+n,32)::cidr,1,$1,now(),2,'qa',true,64502,'[64500,64501,64502]','{}','ingest-fixture' FROM generate_series(0,9999) n ON CONFLICT DO NOTHING",g)
 while await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty)'):await maintain(c,256)
 pinned=await finish_catchup(c)
 old=await c.fetchval('SELECT count(*) FROM inet_incremental.routes_at($1)',pinned)
 await c.execute("UPDATE routing_routes SET event_at=event_at+interval '1 second' WHERE observer='http-0' AND prefix='198.19.0.0/32'")
 tx=block.transaction();await tx.start();await block.fetchrow("SELECT id FROM routing_observers WHERE id='http-0' FOR UPDATE")
 worker=asyncio.create_task(run(c,seconds=60,batch=64))
 await asyncio.sleep(.7);await tx.commit()
 commits=[];events=0;started=time.monotonic();rng=random.Random(99)
 while time.monotonic()-started<40:
  at=time.monotonic();n=rng.randrange(0,9950)
  async with w.transaction():
   await w.fetchrow("SELECT id FROM routing_observers WHERE id='http-0' FOR UPDATE")
   result=await w.execute("UPDATE routing_routes SET event_at=event_at+interval '1 second',origin_asn=64502+$1,path=jsonb_build_array(64500,64501,64502+$1),present=true WHERE observer='http-0' AND prefix>=set_masklen('198.19.0.0'::inet+$2::int,32)::cidr AND prefix<set_masklen('198.19.0.0'::inet+($2::int+50),32)::cidr",events%3,n)
  events+=int(result.split()[-1]);commits.append(time.monotonic()-at)
  await asyncio.sleep(max(0,.25-(time.monotonic()-at)))
 result=await worker
 assert result['timeouts']>=1,'Lock conflict must retry and preserve dirty work'
 assert result['status']['queued']==result['status']['resets']==0,result
 assert not await c.fetchval('SELECT EXISTS((SELECT '+FIELDS+' FROM inet_incremental.current_routes EXCEPT '+CURRENT+') UNION ALL ('+CURRENT+' EXCEPT SELECT '+FIELDS+' FROM inet_incremental.current_routes))')
 assert await c.fetchval('SELECT count(*) FROM inet_incremental.routes_at($1)',pinned)==old
 assert await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.publications WHERE ready AND id>$1)',pinned)
 report={'pass':True,'events':events,'writer_transactions':len(commits),'writer_p95_seconds':round(sorted(commits)[int(.95*(len(commits)-1))],4),'writer_max_seconds':round(max(commits),4),'max_catalogue_batch_seconds':result['max_batch_seconds'],'lock_retries':result['timeouts'],'end_queued':0,'exact_routes':True,'pinned_history_preserved':True,'scope':'small committed synthetic DEV fixture; includes artificial observer-lock conflict; not full-scale capacity certification'}
 Path('/tmp/inet-catalogue-ingest.json').write_text(json.dumps(report,indent=2));print('PASS_INGEST',json.dumps(report),flush=True)
 await block.close();await w.close();await c.close()
asyncio.run(main())
