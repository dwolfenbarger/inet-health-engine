"""Read-only UAT export/measurement pilot. Never deletes rows or changes guards."""
import argparse,asyncio,fcntl,json,os,socket,sys,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path

BASE=Path('/var/lib/inet-health/retention-profile')
ROOT=Path('/home/david/inet-health-engine')

def setup():
    if socket.gethostname() not in {'inet-health-uat','inet-health-dev1'}:raise SystemExit('Full raw profiling is development/UAT only')
    for line in (ROOT/'.env').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
    from urllib.parse import urlsplit
    if urlsplit(os.environ['TIMESCALE_DSN']).hostname not in {'localhost','127.0.0.1','::1'}:
        raise SystemExit('Pilot refuses non-local database')

def normalize(row):
    result=dict(row)
    for key in ['payload','attrs','path','detail','audit','raw_data']:
        if isinstance(result.get(key),str):
            try:result[key]=json.loads(result[key])
            except json.JSONDecodeError:pass
    return result

async def stats(conn):
    tables=['routing_messages','routing_routes','bgp_updates','source_evidence']
    rows=await conn.fetch('''SELECT relname,n_live_tup,pg_total_relation_size(relid) AS bytes
        FROM pg_stat_user_tables WHERE schemaname='public' AND relname=ANY($1::text[])''',tables)
    import shutil
    return {'at':datetime.now(timezone.utc).isoformat(),'source_host':socket.gethostname(),
        'database_bytes':await conn.fetchval('SELECT pg_database_size(current_database())'),
        'root_free_bytes':shutil.disk_usage('/').free,'tables':[dict(r) for r in rows]}

async def measure(conn):
    from collectors.archive_profile import atomic_json
    BASE.mkdir(parents=True,exist_ok=True)
    with (BASE/'.measure.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state_path=BASE/'study.json';now=datetime.now(timezone.utc)
        if state_path.exists():state=json.loads(state_path.read_text())
        else:
            state={'started_at':now.isoformat(),'until':(now+timedelta(hours=72)).isoformat(),
                'scope':'Read-only size/estimate measurements, not full-table counts'}
            atomic_json(state_path,state)
        if now>=datetime.fromisoformat(state['until']):
            print('MEASUREMENT_WINDOW_COMPLETE',state['until'],flush=True);return False
        target=BASE/'measurements.jsonl'
        if target.exists() and target.stat().st_size>256*1024:raise RuntimeError('Measurement file budget')
        item=await stats(conn)
        with target.open('a') as output:output.write(json.dumps(item,sort_keys=True)+'\n');output.flush();os.fsync(output.fileno())
        print('RETENTION_MEASURED',json.dumps(item),flush=True);return True

async def export(conn,mode):
    from collectors.archive_profile import Bundle,JournalCoverage,verify
    BASE.mkdir(parents=True,exist_ok=True);runs=BASE/'runs';runs.mkdir(exist_ok=True)
    name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    b=Bundle(runs/name,max_raw=4*1024**3 if mode=='full-journal' else 128*1024**2)
    metadata={'source_host':socket.gethostname(),'mode':mode,'sample_only':mode!='full-journal',
        'scope':'Development evidence preservation/profile; no deletion eligibility',
        'selected_data':[]}
    try:
        async with conn.transaction(isolation='repeatable_read',readonly=True):
            await conn.execute("SET LOCAL statement_timeout='8s'")
            metadata['snapshot_at']=(await conn.fetchval('SELECT current_timestamp')).isoformat()
            metadata['storage_before']=await stats(conn)
            if mode=='full-journal':
                rows=await conn.fetch("SELECT id,sequence,messages FROM routing_observers WHERE mode='ris-live'")
                expected={r['id']:{'sequence':r['sequence'],'messages':r['messages']} for r in rows}
                coverage=JournalCoverage(expected)
                async for row in conn.cursor('SELECT * FROM routing_messages',prefetch=100):
                    record=normalize(row);coverage.add(record);b.add('routing_messages',record)
                metadata['journal_expected']=expected;metadata['journal_actual']=coverage.finish()
                metadata['selected_data'].append({'table':'routing_messages','selection':'Every row in the read-only repeatable-read snapshot'})
            else:
                selections=[('bgp_updates','SELECT * FROM bgp_updates TABLESAMPLE SYSTEM (0.05) REPEATABLE (23) LIMIT 10000'),
                    ('routing_routes','SELECT * FROM routing_routes TABLESAMPLE SYSTEM (0.1) REPEATABLE (23) LIMIT 10000')]
                for dataset,sql in selections:
                    async for row in conn.cursor(sql,prefetch=100):b.add(dataset,normalize(row))
                    metadata['selected_data'].append({'table':dataset,'selection':sql,'bias':'Block sample, not a complete time window or random-record estimator'})
                sources=await conn.fetch('SELECT source FROM source_status ORDER BY source LIMIT 12')
                for source in sources:
                    async for row in conn.cursor('SELECT * FROM source_evidence WHERE source=$1 ORDER BY observed_at DESC LIMIT 200',source['source'],prefetch=50):
                        b.add('source_evidence',normalize(row))
                metadata['selected_data'].append({'table':'source_evidence','selection':'Latest up to200 rows per configured source; overlap/selection bias explicit'})
        manifest=b.finish(metadata)
        proof=verify(b.root)
        print('PROFILE_COMPLETE',json.dumps({'run':str(b.root),'manifest':str(b.root/'manifest.json'),**proof}),flush=True)
    except BaseException:
        b.abort();raise

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['measure','study','full-journal','sample'])
    parser.add_argument('--source',default='/tmp/inet-retention-candidate');args=parser.parse_args()
    sys.path.insert(0,str(Path(args.source)/'collectors'));setup()
    import asyncpg
    while True:
        conn=await asyncpg.connect(os.environ['TIMESCALE_DSN'],command_timeout=8)
        try:
            if args.mode in {'measure','study'}:active=await measure(conn)
            else:await export(conn,args.mode)
        finally:await conn.close()
        if args.mode!='study' or active is False:break
        await asyncio.sleep(3600)

if __name__=='__main__':asyncio.run(main())
