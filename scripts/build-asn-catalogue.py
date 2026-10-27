"""Publish an immutable investigation generation from one repeatable-read snapshot.

Runs as postgres via local peer authentication. Keeps two published generations;
old page tokens return explicit expiry. Does not modify source routing evidence.
"""
import asyncio,json,shutil,sys,time
sys.path.insert(0,'/opt/inet-journal-runtime/vendor')
import asyncpg
async def retire_old(c):
 # Only derived serving copies expire, never source journal, routes or archive evidence.
 old=await c.fetch('SELECT id FROM inet_catalog.generations WHERE published_at IS NOT NULL ORDER BY id DESC OFFSET 2')
 for row in old:
  async with c.transaction():
   await c.execute(f"DROP TABLE IF EXISTS inet_catalog.members_g{int(row['id'])}")
   await c.execute(f"DROP TABLE inet_catalog.routes_g{int(row['id'])}")
   await c.execute('DELETE FROM inet_catalog.asns WHERE generation=$1',row['id'])
   await c.execute('DELETE FROM inet_catalog.generations WHERE id=$1',row['id'])
async def main():
 c=await asyncpg.connect(database='inethealth',user='postgres',host='/var/run/postgresql',command_timeout=900)
 try:
  if not await c.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-asn-catalogue'))"):raise RuntimeError('Catalogue builder already active')
  await c.execute("SET lock_timeout='5s'")
  await retire_old(c)
  if shutil.disk_usage('/').free<35*1024**3:raise RuntimeError('Root reserve')
  size=await c.fetchval("SELECT coalesce(sum(pg_total_relation_size(relid)),0) FROM (SELECT relid,isleaf FROM pg_partition_tree('inet_catalog.routes') UNION ALL SELECT relid,isleaf FROM pg_partition_tree('inet_catalog.memberships')) parts WHERE isleaf")
  incoming=await c.fetchval("SELECT greatest(coalesce(max(pg_total_relation_size(relid)),0)*2,8*1024^3)::bigint FROM (SELECT relid,isleaf FROM pg_partition_tree('inet_catalog.routes') UNION ALL SELECT relid,isleaf FROM pg_partition_tree('inet_catalog.memberships')) parts WHERE isleaf")
  if size+incoming>24*1024**3:raise RuntimeError('Catalogue peak budget24GiB would be exceeded; retain published view')
  if shutil.disk_usage('/').free<35*1024**3+incoming:raise RuntimeError('Incoming generation would consume root reserve')
  await c.execute("SET work_mem='32MB'; SET temp_file_limit='4GB'; SET lock_timeout='5s'; SET jit=off; SET maintenance_work_mem='128MB'")
  async with c.transaction(isolation='repeatable_read'):
   gen=await c.fetchval('INSERT INTO inet_catalog.generations DEFAULT VALUES RETURNING id')
   table='inet_catalog.routes_g'+str(gen)
   started=time.monotonic()
   # Bulk load without per-row index maintenance; attach creates indexes after the copy.
   await c.execute(f'CREATE TABLE {table} (LIKE inet_catalog.routes INCLUDING DEFAULTS INCLUDING CONSTRAINTS)')
   await c.execute(f'ALTER TABLE {table} ADD CONSTRAINT routes_g{gen}_scope CHECK (generation={gen})')
   # Unknown AS_SET membership remains in raw JSON, never flattened into ordered transit.
   result=await c.execute(f'''INSERT INTO {table}
    SELECT $1,r.observer,r.prefix,r.origin_asn,o.peer_asn,o.collector,
      ARRAY(SELECT DISTINCT a FROM unnest(p.asns) a WHERE a IS DISTINCT FROM r.origin_asn),
      ARRAY(SELECT DISTINCT a FROM unnest(p.asns||ARRAY[r.origin_asn,o.peer_asn]) a WHERE a IS NOT NULL),
      family(r.prefix),r.event_at,r.path,r.attrs,r.evidence_id,r.rank
    FROM routing_routes r JOIN routing_observers o ON o.id=r.observer
    CROSS JOIN LATERAL (SELECT ARRAY(SELECT DISTINCT (v#>>'{{}}')::bigint FROM jsonb_array_elements(r.path) v
      WHERE jsonb_typeof(v)='number' AND (v#>>'{{}}') ~ '^[0-9]{{1,10}}$' AND CASE WHEN (v#>>'{{}}') ~ '^[0-9]{{1,10}}$' THEN (v#>>'{{}}')::bigint BETWEEN 1 AND 4294967295 ELSE false END) AS asns OFFSET 0) p
    WHERE r.present AND r.epoch=o.epoch AND r.generation=o.generation''',gen)
   print('COPIED',gen,result,round(time.monotonic()-started,2),flush=True)
   if shutil.disk_usage('/').free<35*1024**3:raise RuntimeError('Root reserve after copy')
   await c.execute(f'ALTER TABLE inet_catalog.routes ATTACH PARTITION {table} FOR VALUES IN ({gen})')
   print('INDEXED',gen,round(time.monotonic()-started,2),flush=True)
   actual=await c.fetchval('SELECT pg_total_relation_size($1::text::regclass)',table)
   if size+actual>24*1024**3 or shutil.disk_usage('/').free<35*1024**3:raise RuntimeError('Catalogue budget after indexing')
   member_table='inet_catalog.members_g'+str(gen)
   await c.execute(f'CREATE TABLE {member_table} (LIKE inet_catalog.memberships INCLUDING DEFAULTS INCLUDING CONSTRAINTS)')
   await c.execute(f'ALTER TABLE {member_table} ADD CONSTRAINT members_g{gen}_scope CHECK (generation={gen})')
   await c.execute(f'''INSERT INTO {member_table}
    SELECT generation,a,observer,prefix,(CASE WHEN origin_asn=a THEN 1 ELSE 0 END
     +CASE WHEN transit @> ARRAY[a] THEN 2 ELSE 0 END+CASE WHEN peer_asn=a THEN 4 ELSE 0 END)::smallint
    FROM inet_catalog.routes CROSS JOIN LATERAL unnest(members) a WHERE generation=$1''',gen)
   await c.execute(f'ALTER TABLE inet_catalog.memberships ATTACH PARTITION {member_table} FOR VALUES IN ({gen})')
   await c.execute(f'ANALYZE {member_table}')
   print('MEMBERS_INDEXED',gen,round(time.monotonic()-started,2),flush=True)
   actual+=await c.fetchval('SELECT pg_total_relation_size($1::text::regclass)',member_table)
   if size+actual>24*1024**3 or shutil.disk_usage('/').free<35*1024**3:raise RuntimeError('Catalogue budget after memberships')
   await c.execute('''INSERT INTO inet_catalog.asns
    SELECT generation,a,count(*),count(DISTINCT prefix),
      count(*) FILTER(WHERE origin_asn=a),count(*) FILTER(WHERE transit @> ARRAY[a]),
      count(*) FILTER(WHERE peer_asn=a),count(*) FILTER(WHERE family=4),count(*) FILTER(WHERE family=6),count(DISTINCT observer)
    FROM inet_catalog.routes CROSS JOIN LATERAL unnest(members) a
    WHERE generation=$1 GROUP BY generation,a''',gen)
   obs=await c.fetch('''SELECT o.id,o.collector,o.peer_asn,host(o.peer_ip) peer_ip,o.metadata,o.event_at,o.baseline_at,
    o.stream_state,o.baseline_state,o.error_code,(SELECT count(*) FROM routing_gaps g WHERE g.observer=o.id AND g.state NOT IN ('repaired','snapshot_superseded')) pending_gaps
    FROM routing_observers o ORDER BY id''')
   counts=await c.fetchrow('''SELECT count(*) routes,count(DISTINCT prefix) prefixes,count(DISTINCT origin_asn) origins,
    count(DISTINCT observer) observers FROM inet_catalog.routes WHERE generation=$1''',gen)
   summary=dict(counts);summary['asns']=await c.fetchval('SELECT count(*) FROM inet_catalog.asns WHERE generation=$1',gen)
   facets=await c.fetch('SELECT observer,collector,family,count(*) routes FROM inet_catalog.routes WHERE generation=$1 GROUP BY observer,collector,family',gen)
   summary['facets']=[dict(x) for x in facets]
   await c.execute('UPDATE inet_catalog.generations SET published_at=clock_timestamp(),observers=$2::jsonb,summary=$3::jsonb WHERE id=$1',gen,json.dumps([dict(x) for x in obs],default=str),json.dumps(summary))
   await c.execute(f'ANALYZE {table}')
   await c.execute('ANALYZE inet_catalog.asns')
  print('PUBLISHED',gen,json.dumps(summary),'elapsed',round(time.monotonic()-started,2),flush=True)
  await retire_old(c)
 finally:await c.close()
if __name__=='__main__':asyncio.run(main())
