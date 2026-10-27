"""Rollback-only synthetic scale comparison in the dedicated QA database."""
import asyncio,json,sys,time,uuid
from pathlib import Path
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(Path(__file__).resolve().parents[1]/'collectors')]
import asyncpg
from collectors.catalogue_incremental import install,seed,maintain,CURRENT,FIELDS
async def main():
 c=await asyncpg.connect(database='inet_catalogue_qa',user='postgres',host='/var/run/postgresql',command_timeout=180)
 assert await c.fetchval('SELECT current_database()')=='inet_catalogue_qa'
 await install(c);t=c.transaction();await t.start()
 try:
  await c.execute("SET LOCAL work_mem='32MB'; SET LOCAL lock_timeout='3s'; SET LOCAL statement_timeout='180s'")
  g=uuid.UUID(int=22)
  for obs,peer,ip in [('qa-scale-a',64500,'198.51.100.1'),('qa-scale-b',64501,'198.51.100.2')]:
   await c.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation) VALUES($1,'fixture',$1,$2::inet,$3,'mrt','{}',$4)",obs,ip,peer,g)
   await c.execute("""INSERT INTO routing_routes SELECT $1,set_masklen('198.18.0.0'::inet+n,32)::cidr,1,$2,now(),2,'qa',true,64502,'[64500,64501,64502]','{}','synthetic-scale' FROM generate_series(0,19999) n""",obs,g)
  await seed(c)
  pinned=await c.fetchval('SELECT max(id) FROM inet_incremental.publications')
  count=await c.fetchval('SELECT count(*) FROM inet_incremental.current_routes')
  original=await c.fetchval('SELECT sum(origins) FROM inet_incremental.asns_at($1)',pinned)
  await c.execute("UPDATE routing_routes SET origin_asn=64503,path='[64500,64501,64503]',event_at=event_at+interval '1 second' WHERE observer LIKE 'qa-scale-%' AND prefix>='198.18.0.0/32'::cidr AND prefix<'198.18.0.200/32'::cidr")
  sub=c.transaction();await sub.start();at=time.monotonic();wal=await c.fetchval('SELECT pg_current_wal_insert_lsn()::text')
  await c.execute('CREATE TABLE inet_incremental.full_routes AS '+CURRENT)
  for sql in ['CREATE UNIQUE INDEX full_key ON inet_incremental.full_routes(observer,prefix)',
   'CREATE INDEX full_origin ON inet_incremental.full_routes(origin_asn,observer,prefix)',
   'CREATE INDEX full_peer ON inet_incremental.full_routes(peer_asn,observer,prefix)',
   'CREATE INDEX full_members ON inet_incremental.full_routes USING gin(members)',
   'CREATE INDEX full_transit ON inet_incremental.full_routes USING gin(transit)',
   'CREATE INDEX full_prefix ON inet_incremental.full_routes USING gist(prefix inet_ops)']:
   await c.execute(sql)
  await c.execute("""CREATE TABLE inet_incremental.full_memberships AS SELECT a asn,observer,prefix,((CASE WHEN origin_asn=a THEN 1 ELSE 0 END)+(CASE WHEN transit @> ARRAY[a] THEN 2 ELSE 0 END)+(CASE WHEN peer_asn=a THEN 4 ELSE 0 END))::smallint roles FROM inet_incremental.full_routes CROSS JOIN LATERAL unnest(members) a;
  CREATE UNIQUE INDEX full_membership_key ON inet_incremental.full_memberships(asn,observer,prefix);
  CREATE TABLE inet_incremental.full_asns AS SELECT asn,count(*) routes,count(DISTINCT prefix) prefixes,count(*) FILTER(WHERE (roles&1)<>0) origins,count(*) FILTER(WHERE (roles&2)<>0) transits,count(*) FILTER(WHERE (roles&4)<>0) peers,count(*) FILTER(WHERE family(prefix)=4) ipv4,count(*) FILTER(WHERE family(prefix)=6) ipv6,count(DISTINCT observer) observers FROM inet_incremental.full_memberships GROUP BY asn;
  CREATE UNIQUE INDEX full_asn_key ON inet_incremental.full_asns(asn);""")
  full={'seconds':time.monotonic()-at,'wal_bytes':int(await c.fetchval('SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),$1::text::pg_lsn)',wal))}
  await sub.rollback()
  at=time.monotonic();wal=await c.fetchval('SELECT pg_current_wal_insert_lsn()::text');processed=0
  while await c.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty)'):
   processed+=(await maintain(c,256))['processed']
  incremental={'seconds':time.monotonic()-at,'wal_bytes':int(await c.fetchval('SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),$1::text::pg_lsn)',wal)),'processed':processed}
  assert await c.fetchval('SELECT count(*) FROM inet_incremental.current_routes')==count
  assert not await c.fetchval('SELECT EXISTS((SELECT '+FIELDS+' FROM inet_incremental.current_routes EXCEPT '+CURRENT+') UNION ALL ('+CURRENT+' EXCEPT SELECT '+FIELDS+' FROM inet_incremental.current_routes))')
  assert await c.fetchval('SELECT sum(origins) FROM inet_incremental.asns_at($1)',pinned)==original
  assert await c.fetchval("SELECT count(*) FROM inet_incremental.routes_at($1) WHERE observer LIKE 'qa-scale-%' AND origin_asn=64503",pinned)==0
  await c.execute('ANALYZE routing_routes')
  read={}
  for variant in ['original','brin']:
   check=c.transaction();await check.start()
   if variant=='brin':
    await c.execute('DROP INDEX routing_routes_recent; DROP INDEX routing_routes_observer_time; CREATE INDEX scale_time_brin ON routing_routes USING brin(event_at)')
   plan=json.loads(await c.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT observer,prefix,event_at FROM routing_routes ORDER BY event_at DESC,observer,prefix LIMIT 100'))[0]
   read[variant]={'execution_ms':plan['Execution Time'],'shared_hit_blocks':plan['Plan'].get('Shared Hit Blocks',0),'shared_read_blocks':plan['Plan'].get('Shared Read Blocks',0)}
   await check.rollback()
  print('PASS_SCALE',json.dumps({'routes':count,'changed_keys':processed,'full':full,'incremental':incremental,'exact_routes_and_pinned_history':True,'recency_read_comparison':read}))
 finally:await t.rollback();await c.close()
asyncio.run(main())
