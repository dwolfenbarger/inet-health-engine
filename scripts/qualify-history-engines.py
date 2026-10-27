"""Private projected-event benchmark: PostgreSQL versus embedded ClickHouse.

This is not a ClickHouse server/HA/concurrent-serving qualification. CSV input is
private, capped and hashed. No provider access and no production modifications.
"""
import asyncio,csv,hashlib,json,socket,sys,time,statistics,os
from pathlib import Path
sys.path[:0]=['/opt/inet-journal-runtime/vendor','/opt/inet-write-opt/analytics-vendor']
import asyncpg
from chdb import session
DB='inet_history_engine_qa';CSV=Path('/tmp/inet-history-sample.csv');MANIFEST=Path('/tmp/inet-history-sample.json')
def io(pid='self'):
 return {k:int(v) for k,v in (x.split(':',1) for x in Path(f'/proc/{pid}/io').read_text().splitlines())}
def delta(a,b):return {k:b[k]-a[k] for k in ['read_bytes','write_bytes','rchar','wchar']}
async def main():
 assert socket.gethostname()=='inet-health-dev1'
 os.umask(0o077);manifest=json.loads(MANIFEST.read_text());assert hashlib.sha256(CSV.read_bytes()).hexdigest()==manifest['sha256']
 c=await asyncpg.connect(database=DB,user='postgres',host='/var/run/postgresql',command_timeout=60)
 assert await c.fetchval('SELECT current_database()')==DB
 await c.execute("SET statement_timeout='45s'; SET lock_timeout='1s'; SET jit=off; SET max_parallel_workers_per_gather=2")
 await c.execute('DROP TABLE IF EXISTS history_events; CREATE TABLE history_events(ts double precision,prefix text,origin_asn bigint,change_type text,collector text,peer_asn bigint)')
 root=Path('/tmp/inet-history-clickhouse-qa');root.mkdir(exist_ok=True)
 ch=session.Session(str(root))
 ch.query('DROP TABLE IF EXISTS history_events')
 ch.query('CREATE TABLE history_events(ts Float64,prefix String,origin_asn Nullable(UInt64),change_type LowCardinality(String),collector LowCardinality(String),peer_asn Nullable(UInt64)) ENGINE=MergeTree ORDER BY (ts,prefix) SETTINGS allow_nullable_key=1')
 # Same bounded input divided into five reasonably sized ingestion batches.
 files=[]
 with CSV.open(newline='') as f:
  rows=csv.reader(f);header=next(rows);batch=[]
  for row in rows:
   batch.append(row)
   if len(batch)==50000:
    p=Path(f'/tmp/inet-history-batch-{len(files)}.csv')
    with p.open('w',newline='') as out:w=csv.writer(out);w.writerow(header);w.writerows(batch)
    files.append(p);batch=[]
  if batch:
   p=Path(f'/tmp/inet-history-batch-{len(files)}.csv')
   with p.open('w',newline='') as out:w=csv.writer(out);w.writerow(header);w.writerows(batch)
   files.append(p)
 report={'scope':'250k most recent projected legacy sample; same six columns, five bulk batches; warm-cache repeats; embedded ClickHouse, not server qualification','sample':manifest,'versions':{'postgres':await c.fetchval('SELECT version()'),'clickhouse':str(ch.query('SELECT version()','CSV')).strip()},'ingestion':{},'queries':{}}
 pgpid=await c.fetchval('SELECT pg_backend_pid()');a=io(pgpid);at=time.monotonic();wal=await c.fetchval('SELECT pg_current_wal_insert_lsn()::text')
 for p in files:await c.copy_to_table('history_events',source=str(p),format='csv',header=True)
 report['ingestion']['postgres_copy_seconds']=time.monotonic()-at
 at=time.monotonic()
 for name,cols in [('history_time','ts DESC'),('history_origin','origin_asn,ts DESC'),('history_prefix','prefix,ts DESC')]:await c.execute('CREATE INDEX '+name+' ON history_events('+cols+')')
 await c.execute('ANALYZE history_events')
 report['ingestion']['postgres_index_seconds']=time.monotonic()-at
 report['ingestion']['postgres_backend_io']=delta(a,io(pgpid))
 report['ingestion']['postgres_cluster_wal_bytes']=int(await c.fetchval('SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),$1::text::pg_lsn)',wal))
 a=io();at=time.monotonic()
 for p in files:ch.query(f"INSERT INTO history_events SELECT * FROM file('{p}',CSVWithNames,'ts Float64,prefix String,origin_asn Nullable(UInt64),change_type String,collector String,peer_asn Nullable(UInt64)') SETTINGS max_threads=2,max_memory_usage=1073741824")
 report['ingestion']['clickhouse_insert_seconds']=time.monotonic()-at
 # Let natural merges settle, without forcing OPTIMIZE FINAL.
 deadline=time.monotonic()+20
 while int(str(ch.query('SELECT count() FROM system.merges','CSV')).strip()) and time.monotonic()<deadline:await asyncio.sleep(.1)
 report['ingestion']['clickhouse_process_io']=delta(a,io())
 pgcount=await c.fetchval('SELECT count(*) FROM history_events');chcount=int(str(ch.query('SELECT count() FROM history_events','CSV')).strip());assert pgcount==chcount==250000
 end=await c.fetchval('SELECT max(ts) FROM history_events');cut=end-3600
 queries={
 'origin_change_counts':('SELECT coalesce(origin_asn,0) asn,change_type,count(*) n FROM history_events GROUP BY asn,change_type ORDER BY asn,change_type','SELECT ifNull(origin_asn,0) asn,change_type,count() n FROM history_events GROUP BY asn,change_type ORDER BY asn,change_type'),
 'minute_collector_counts':('SELECT floor(ts/60)::bigint minute,collector,count(*) n FROM history_events GROUP BY minute,collector ORDER BY minute,collector','SELECT toInt64(floor(ts/60)) minute,collector,count() n FROM history_events GROUP BY minute,collector ORDER BY minute,collector'),
 'busy_prefixes':('SELECT prefix,count(*) n FROM history_events GROUP BY prefix ORDER BY n DESC,prefix LIMIT 100','SELECT prefix,count() n FROM history_events GROUP BY prefix ORDER BY n DESC,prefix LIMIT 100'),
 'asn_hour':(f'SELECT floor(ts/60)::bigint minute,change_type,count(*) n FROM history_events WHERE origin_asn=13335 AND ts>{cut} GROUP BY minute,change_type ORDER BY minute,change_type',f'SELECT toInt64(floor(ts/60)) minute,change_type,count() n FROM history_events WHERE origin_asn=13335 AND ts>{cut} GROUP BY minute,change_type ORDER BY minute,change_type')}
 for name,(pgsql,chsql) in queries.items():
  times={'postgres':[],'clickhouse':[]};expected=None
  for repeat in range(5):
   at=time.monotonic();pgrows=[list(r.values()) for r in await c.fetch(pgsql)];times['postgres'].append(time.monotonic()-at)
   at=time.monotonic();data=json.loads(str(ch.query(chsql+' SETTINGS max_threads=2,max_memory_usage=1073741824','JSONCompact')));chrows=data['data'];times['clickhouse'].append(time.monotonic()-at)
   assert pgrows==chrows,(name,'Query results differ')
  report['queries'][name]={'exact_results':True,'rows':len(pgrows),'postgres_seconds':times['postgres'],'clickhouse_seconds':times['clickhouse'],'postgres_warm_median_seconds':statistics.median(times['postgres'][1:]),'clickhouse_warm_median_seconds':statistics.median(times['clickhouse'][1:])}
 report['storage']={'postgres_relation_bytes':await c.fetchval("SELECT pg_total_relation_size('history_events')"),'clickhouse_parts':json.loads(str(ch.query("SELECT sum(bytes_on_disk) bytes,count() parts FROM system.parts WHERE table='history_events' AND active",'JSONCompact')))['data']}
 report['pass']=True
 Path('/tmp/inet-history-engine-comparison.json').write_text(json.dumps(report,indent=2,default=str))
 print('PASS_HISTORY_ENGINES',json.dumps(report,default=str),flush=True)
 ch.close();await c.close()
asyncio.run(main())
