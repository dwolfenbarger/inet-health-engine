"""Same-host, resource-bounded synthetic engine trial. No production data input."""
import csv,hashlib,json,subprocess,time,statistics,os
from pathlib import Path
ROOT=Path.cwd()/'synthetic-history-20261027';ROOT.mkdir(exist_ok=True)
SUFFIX=os.environ.get('INET_QA_SUFFIX','20261027');assert all(x.isalnum() or x=='-' for x in SUFFIX)
PG='inet-pg-qa-'+SUFFIX;CH='inet-ch-qa-'+SUFFIX;PGVOL=PG+'-data';CHVOL=CH+'-data'
REPORT=ROOT/'comparison.json'
def cmd(args,stdin=None,timeout=120):
 r=subprocess.run(args,input=stdin,text=True,capture_output=True,timeout=timeout,encoding='utf-8')
 if r.returncode:raise RuntimeError((args,r.returncode,r.stdout[-1000:],r.stderr[-2000:]))
 return r.stdout

def pg(sql):return json.loads(cmd(['docker','exec','-i',PG,'psql','-XAt','-v','ON_ERROR_STOP=1','-U','postgres','-d','postgres'],sql))
def ch(sql):
 out=cmd(['docker','exec','-i',CH,'clickhouse-client','--format','JSONCompact','--query',sql])
 return json.loads(out)['data'] if out.strip() else []

def main():
 # Never adopt an existing unrelated container or volume.
 names=cmd(['docker','ps','-a','--format','{{.Names}}']).splitlines()
 assert PG not in names and CH not in names,'Qualification names already exist'
 volumes=cmd(['docker','volume','ls','--format','{{.Name}}']).splitlines()
 assert PGVOL not in volumes and CHVOL not in volumes,'Qualification volumes already exist'
 csvpath=ROOT/'synthetic.csv';n=1000000
 with csvpath.open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['ts','prefix','origin_asn','change_type','collector','peer_asn'])
  for i in range(n):
   # Deterministic mixed IPv4/IPv6, ASN and time distribution, not live evidence.
   p=f'2001:db8:{i%65536:x}::/48' if i%5==0 else f'198.{18+(i//65536)%2}.{(i//256)%256}.{i%256}/32'
   w.writerow([1700000000+i//200,p,64512+(i*17)%10000,'withdraw' if i%7==0 else 'announce','fixture-'+str(i%5),64500+i%4])
 sha=hashlib.sha256(csvpath.read_bytes()).hexdigest();print('SYNTHETIC_INPUT',n,sha,flush=True)
 for v in [PGVOL,CHVOL]:cmd(['docker','volume','create','--label','inet.synthetic-qualification=20261027',v])
 bind=str(ROOT.resolve())+':/input:ro'
 created=[]
 try:
  cmd(['docker','run','-d','--name',PG,'--label','inet.synthetic-qualification=20261027','--network','none','--cpus','2','--memory','2g','--shm-size','256m','-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_INITDB_ARGS=--locale=C','-v',PGVOL+':/var/lib/postgresql/data','-v',bind,'postgres:17']);created.append(PG)
  cmd(['docker','run','-d','--name',CH,'--label','inet.synthetic-qualification=20261027','--network','none','--cpus','2','--memory','2g','-e','CLICKHOUSE_SKIP_USER_SETUP=1','-v',CHVOL+':/var/lib/clickhouse','clickhouse/clickhouse-server:26.8']);created.append(CH)
  for _ in range(60):
   try:pg('SELECT 1');ch('SELECT 1');break
   except RuntimeError:time.sleep(1)
  else:raise RuntimeError('Database startup failed')
  report={'scope':'synthetic 1M six-column rows; same Vengeance Docker host,2CPU/2GiB each, no ports published/networknone; warm repeats; not real-data or production-IOPS certification','input_sha256':sha,'rows':n,'versions':{'postgres':pg('SELECT to_json(version())'),'clickhouse':ch('SELECT version()')},'images':json.loads(cmd(['docker','inspect','--format','{{json .Image}}',PG])),'ingestion':{},'queries':{}}
  at=time.monotonic();cmd(['docker','exec','-i',PG,'psql','-XAt','-v','ON_ERROR_STOP=1','-U','postgres'],"CREATE TABLE events(ts double precision,prefix text,origin_asn bigint,change_type text,collector text,peer_asn bigint); COPY events FROM '/input/synthetic.csv' WITH(FORMAT csv,HEADER true);")
  report['ingestion']['postgres_copy_seconds']=time.monotonic()-at
  at=time.monotonic();cmd(['docker','exec','-i',PG,'psql','-XAt','-v','ON_ERROR_STOP=1','-U','postgres'],"CREATE INDEX event_time ON events(ts DESC); CREATE INDEX event_origin ON events(origin_asn,ts DESC); CREATE INDEX event_prefix ON events(prefix,ts DESC); ANALYZE events;")
  report['ingestion']['postgres_index_seconds']=time.monotonic()-at
  cmd(['docker','cp',str(csvpath),CH+':/var/lib/clickhouse/user_files/synthetic.csv'])
  ch('CREATE TABLE events(ts Float64,prefix String,origin_asn UInt64,change_type LowCardinality(String),collector LowCardinality(String),peer_asn UInt64) ENGINE=MergeTree ORDER BY (ts,prefix)')
  at=time.monotonic();ch("INSERT INTO events SELECT * FROM file('synthetic.csv',CSVWithNames,'ts Float64,prefix String,origin_asn UInt64,change_type String,collector String,peer_asn UInt64') SETTINGS max_threads=2,max_memory_usage=1073741824")
  report['ingestion']['clickhouse_insert_seconds']=time.monotonic()-at
  assert pg('SELECT to_json(count(*)) FROM events')==ch('SELECT count() FROM events')[0][0]==n
  for name,query in {
   'origin_change_counts':'SELECT origin_asn,change_type,count(*) n FROM events GROUP BY origin_asn,change_type ORDER BY origin_asn,change_type',
   'collector_counts':'SELECT collector,change_type,count(*) n FROM events GROUP BY collector,change_type ORDER BY collector,change_type',
   'busy_prefixes':'SELECT prefix,count(*) n FROM events GROUP BY prefix ORDER BY n DESC,prefix LIMIT 100',
   'asn_history':'SELECT ts,prefix,change_type FROM events WHERE origin_asn=65000 ORDER BY ts,prefix,change_type LIMIT 100'
  }.items():
   pg_times=[];ch_times=[]
   for _ in range(5):
    pgplan=pg('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query);pg_times.append(pgplan[0]['Execution Time']/1000)
    result=json.loads(cmd(['docker','exec','-i',CH,'clickhouse-client','--format','JSONCompact','--query',query+' SETTINGS max_threads=2,max_memory_usage=1073741824']));ch_times.append(result['statistics']['elapsed'])
   expected=pg('SELECT coalesce(json_agg(row_to_json(q)),\'[]\'::json) FROM ('+query+') q')
   assert [list(r.values()) for r in expected]==result['data'],(name,'Result mismatch')
   report['queries'][name]={'exact_results':True,'groups':len(expected),'postgres_warm_median_seconds':statistics.median(pg_times[1:]),'clickhouse_warm_median_seconds':statistics.median(ch_times[1:]),'postgres_seconds':pg_times,'clickhouse_seconds':ch_times}
   print('QUERY',name,json.dumps(report['queries'][name]),flush=True)
  report['storage']={'postgres_table_and_indexes':pg("SELECT to_json(pg_total_relation_size('events'))"),'clickhouse_active_parts':ch("SELECT sum(bytes_on_disk),count() FROM system.parts WHERE table='events' AND active")}
  report['image_ids']=json.loads(cmd(['docker','inspect',PG,CH]))
  # Keep only non-sensitive reproducibility metadata from inspect.
  report['image_ids']=[{'name':r['Name'],'image':r['Image'],'limits':{k:r['HostConfig'][k] for k in ['Memory','NanoCpus','NetworkMode']}} for r in report['image_ids']]
  report['pass']=True;REPORT.write_text(json.dumps(report,indent=2));print('PASS_SYNTHETIC_HISTORY',json.dumps({k:v for k,v in report.items() if k!='image_ids'}),flush=True)
 finally:
  for container in created:
   logs=subprocess.run(['docker','logs',container],capture_output=True,text=True,timeout=10);(ROOT/(container+'.log')).write_text(logs.stdout+logs.stderr)
   cmd(['docker','stop','--time','15',container],timeout=25)
  print('OWN_QUALIFICATION_CONTAINERS_STOPPED_VOLUMES_RETAINED',flush=True)
if __name__=='__main__':main()
