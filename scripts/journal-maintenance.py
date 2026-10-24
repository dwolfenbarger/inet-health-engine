"""Development journal administration. Local peer auth; no credential export."""
import argparse,asyncio,json,os,socket,sys,uuid
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
BASE=Path('/var/lib/inet-health/journal-export')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'collectors'))
from collectors.journal_rotation import prepare,partitions,retire,trim_keys,usage,checked_name
from collectors.archive_profile import Bundle,verify
UTC=timezone.utc
async def export(conn,name):
    checked_name(name)
    BASE.mkdir(exist_ok=True)
    occupied=sum(p.stat().st_size for p in BASE.rglob('*') if p.is_file())
    if occupied>2*1024**3:raise RuntimeError('Local export staging budget')
    run=BASE/(name+'-'+uuid.uuid4().hex[:8]);b=Bundle(run,purpose='journal')
    hourly=Counter();observer_stats={}
    try:
        async with conn.transaction(isolation='repeatable_read',readonly=True):
            await conn.execute("SET LOCAL statement_timeout='15s'")
            segment=await conn.fetchrow('SELECT * FROM routing_journal_segments WHERE name=$1',name)
            if not segment or segment['until_at']>datetime.now(UTC)-timedelta(minutes=5):raise RuntimeError('Segment is still open')
            async for record in conn.cursor(f'SELECT observer,message_id,received_at,event_at,sequence,payload FROM {name}',prefetch=100):
                row=dict(record);row['payload']=json.loads(row['payload']) if isinstance(row['payload'],str) else row['payload']
                b.add('routing_messages',row)
                stats=observer_stats.setdefault(row['observer'],{'observer':row['observer'],'records':0,'first':row['sequence'],'last':row['sequence']})
                stats['records']+=1;stats['first']=min(stats['first'],row['sequence']);stats['last']=max(stats['last'],row['sequence'])
                key=(row['observer'],row['event_at'].astimezone(UTC).replace(minute=0,second=0,microsecond=0).isoformat())
                hourly[key]+=1
            metadata={'segment':name,'environment':'development','from_at':segment['from_at'],'until_at':segment['until_at'],
                'observers':sorted(observer_stats.values(),key=lambda r:r['observer']),
                'routing_activity':[{'observer':o,'hour':h,'frames':n,'metric':'captured RIS frames, not affected networks or health',
                  'complete':False,'contribution_id':name} for (o,h),n in sorted(hourly.items())],
                'source_host':socket.gethostname(),'source_environment':'production' if socket.gethostname()=='inet-health-prod' else 'development',
                'scope':'Exact sealed segment; routing state and epoch/gap/checkpoint metadata remain local. No independent backup claim.'}
        manifest=b.finish(metadata);proof=verify(run)
        return {'run':str(run),'segment':name,'proof':proof}
    except BaseException:b.abort();raise
async def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','list','export','ack','retire','status','cleanup','expired']);p.add_argument('--segment');p.add_argument('--receipt');p.add_argument('--database',required=True);args=p.parse_args()
    if socket.gethostname() not in {'inet-health-uat','inet-health-dev1','inet-health-prod'}:raise SystemExit('Known collector hosts only')
    if socket.gethostname()=='inet-health-prod':
        role=json.loads(Path('/etc/inet-health/journal-nas-ready.json').read_text())
        if role.get('full_archive_access')!='development-only':raise SystemExit('A private development archive publisher is required')
    import asyncpg
    conn=await asyncpg.connect(database=args.database,user='postgres',host='/var/run/postgresql',command_timeout=120)
    try:
        if args.action=='prepare':
            if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-routing-stream'))"):raise RuntimeError('Receiver still active; cannot bootstrap')
            await prepare(conn)
            await conn.execute('UPDATE routing_journal_policy SET enabled=true,updated_at=now() WHERE id')
            result=await usage(conn)
        elif args.action=='list':
            await partitions(conn)
            result=[dict(r) for r in await conn.fetch("SELECT * FROM routing_journal_segments WHERE retired_at IS NULL AND until_at<now()-interval '5 minutes' AND (state<>'archived' OR until_at<now()-interval '24 hours') ORDER BY CASE WHEN state='archived' THEN 1 ELSE 0 END,until_at LIMIT 4")]
        elif args.action=='export':result=await export(conn,args.segment)
        elif args.action in {'ack','retire'}:
            receipt=json.loads(Path(args.receipt).read_text());checked_name(args.segment)
            if receipt.get('segment')!=args.segment or receipt.get('verified') is not True:raise ValueError('Receipt identity')
            if args.action=='retire':result={'retired':await retire(conn,args.segment,receipt)}
            else:
                await conn.execute("UPDATE routing_journal_segments SET receipt=$2::jsonb,state='archived',archived_at=now() WHERE name=$1 AND retired_at IS NULL",args.segment,json.dumps(receipt));result={'acknowledged':args.segment}
        elif args.action=='expired':
            result=[dict(r) for r in await conn.fetch("SELECT name,receipt FROM routing_journal_segments WHERE retired_at IS NOT NULL AND until_at<now()-interval '72 hours' ORDER BY until_at LIMIT 4")]
        elif args.action=='cleanup':
            result={'trimmed_keys':await trim_keys(conn)}
            for path in BASE.glob('routing_messages_*'):
                # Only completed exported staging objects older than an hour.
                if path.is_dir() and (path/'manifest.json').exists() and datetime.now(UTC).timestamp()-path.stat().st_mtime>3600:
                    import shutil;shutil.rmtree(path)
        else:result=await usage(conn)
        print('RESULT_JSON',json.dumps(result,default=str),flush=True)
    finally:await conn.close()
if __name__=='__main__':asyncio.run(main())
