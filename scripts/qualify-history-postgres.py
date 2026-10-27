"""Native PostgreSQL historical projection and summary qualification, DEV only."""
import asyncio,hashlib,json,socket,sys,time,statistics
from pathlib import Path
sys.path.insert(0,'/opt/inet-journal-runtime/vendor')
import asyncpg
async def main():
 assert socket.gethostname()=='inet-health-dev1'
 sample=Path('/tmp/inet-history-sample.csv');meta=json.loads(Path('/tmp/inet-history-sample.json').read_text())
 assert hashlib.sha256(sample.read_bytes()).hexdigest()==meta['sha256']
 c=await asyncpg.connect(database='inet_history_engine_qa',user='postgres',host='/var/run/postgresql',command_timeout=45)
 await c.execute("SET statement_timeout='30s'; SET lock_timeout='1s'; SET jit=off; SET max_parallel_workers_per_gather=2")
 await c.execute('DROP TABLE IF EXISTS history_events,history_bucket_counts; CREATE TABLE history_events(ts double precision,prefix text,origin_asn bigint,change_type text,collector text,peer_asn bigint)')
 at=time.monotonic();await c.copy_to_table('history_events',source=str(sample),format='csv',header=True);copy_seconds=time.monotonic()-at
 at=time.monotonic()
 for name,cols in [('history_time','ts DESC'),('history_origin','origin_asn,ts DESC'),('history_prefix','prefix,ts DESC')]:await c.execute('CREATE INDEX '+name+' ON history_events('+cols+')')
 index_seconds=time.monotonic()-at;await c.execute('ANALYZE history_events')
 assert await c.fetchval('SELECT count(*) FROM history_events')==250000
 at=time.monotonic();await c.execute('''CREATE TABLE history_bucket_counts AS SELECT floor(ts/60)::bigint bucket,origin_asn,change_type,collector,count(*) n FROM history_events GROUP BY bucket,origin_asn,change_type,collector;
 CREATE INDEX history_bucket_key ON history_bucket_counts(bucket,origin_asn,change_type,collector); ANALYZE history_bucket_counts''');summary_seconds=time.monotonic()-at
 queries={
 'origin_change_counts':('SELECT origin_asn,change_type,count(*) n FROM history_events GROUP BY origin_asn,change_type ORDER BY origin_asn,change_type','SELECT origin_asn,change_type,sum(n)::bigint n FROM history_bucket_counts GROUP BY origin_asn,change_type ORDER BY origin_asn,change_type'),
 'bucket_collector_counts':('SELECT floor(ts/60)::bigint bucket,collector,count(*) n FROM history_events GROUP BY bucket,collector ORDER BY bucket,collector','SELECT bucket,collector,sum(n)::bigint n FROM history_bucket_counts GROUP BY bucket,collector ORDER BY bucket,collector')}
 checks={}
 for name,(raw,summary) in queries.items():
  timings={'raw':[],'summary':[]}
  for _ in range(5):
   at=time.monotonic();a=await c.fetch(raw);timings['raw'].append(time.monotonic()-at)
   at=time.monotonic();b=await c.fetch(summary);timings['summary'].append(time.monotonic()-at)
   assert a==b
  checks[name]={'exact_counts':True,'groups':len(a),'raw_warm_median_seconds':statistics.median(timings['raw'][1:]),'summary_warm_median_seconds':statistics.median(timings['summary'][1:]),'samples':timings}
 report={'pass':True,'sample':meta,'rows':250000,'projection_copy_seconds':copy_seconds,'projection_index_seconds':index_seconds,'summary_build_seconds':summary_seconds,'summary_rows':await c.fetchval('SELECT count(*) FROM history_bucket_counts'),'projection_bytes':await c.fetchval("SELECT pg_total_relation_size('history_events')"),'summary_bytes':await c.fetchval("SELECT pg_total_relation_size('history_bucket_counts')"),'queries':checks,'scope':'bounded private six-column legacy sample, warm-cache DEV; summary counts do not replace raw event order, paths, flap/hijack evidence; no production compression/retention change','clickhouse':'not included in this PostgreSQL-only qualification'}
 Path('/tmp/inet-history-postgres-qualification.json').write_text(json.dumps(report,indent=2,default=str));print('PASS_HISTORY_POSTGRES',json.dumps(report,default=str),flush=True);await c.close()
asyncio.run(main())
