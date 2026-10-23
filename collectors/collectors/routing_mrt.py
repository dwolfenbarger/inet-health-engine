"""RIB bootstrap and contiguous MRT replay for selected public observers."""
import argparse,asyncio,hashlib,json,os,re,shutil,signal,time,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlparse
import asyncpg,httpx
from concurrent.futures import ThreadPoolExecutor
from collectors.config import settings
from collectors.db import get_pg_pool,close_pg_pool
from collectors.routing_model import stamp,mrt_change
from collectors.routing_store import register,archive_batch
from collectors.mrt_audit import audit

UTC=timezone.utc
class WaitingArchive(RuntimeError):pass


def months(start,end):
    at=start.replace(day=1,hour=0,minute=0,second=0,microsecond=0);out=[]
    while at<=end:
        out.append(at.strftime('%Y.%m'))
        at=(at.replace(day=28)+timedelta(days=4)).replace(day=1)
    return out

async def manifest(observer,kind,start,end):
    period=300 if observer['mode']=='ris-live' else 900
    prefix='bview' if kind=='rib' and period==300 else 'rib' if kind=='rib' else 'updates' if period==900 else 'updates'
    # RIS uses updates.YYYYMMDD.HHmm.gz (verified current archive listings).
    if period==300:prefix='bview' if kind=='rib' else 'updates'
    base=f"https://data.ris.ripe.net/{observer['collector']}/" if period==300 else observer['archive_base'].rstrip('/')+'/'
    files=[]
    async with httpx.AsyncClient(timeout=40,follow_redirects=True) as client:
        for month in months(start,end):
            directory=base+month+'/'
            if period==900:directory+=('RIBS/' if kind=='rib' else 'UPDATES/')
            response=await client.get(directory);response.raise_for_status()
            suffix='gz' if period==300 else 'bz2'
            pattern=rf'href=["\']({prefix}\.([0-9]{{8}}\.[0-9]{{4}})\.{suffix})["\']'
            for filename,clock in re.findall(pattern,response.text):
                at=datetime.strptime(clock,'%Y%m%d.%H%M').replace(tzinfo=UTC)
                if start<=at<=end:
                    files.append({'url':directory+filename,'at':at,'until':at+timedelta(seconds=period)})
    return sorted({f['url']:f for f in files}.values(),key=lambda f:f['at'])


def covering_files(files,start,until,period):
    at=datetime.fromtimestamp(int(start.timestamp())//period*period,UTC)
    end=datetime.fromtimestamp((int(until.timestamp())//period+1)*period,UTC)
    by_time={f['at']:f for f in files};out=[]
    while at<end:
        if at not in by_time:raise WaitingArchive(f'Missing complete archive interval {at.isoformat()}')
        out.append(by_time[at]);at+=timedelta(seconds=period)
    return out

async def download(url,cache):
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in ['data.ris.ripe.net','archive.routeviews.org']:
        raise ValueError('Unapproved MRT archive origin')
    if shutil.disk_usage(cache).free<20*1024**3:raise RuntimeError('DiskCapacityPause')
    destination=cache/(hashlib.sha256(url.encode()).hexdigest()+('.gz' if url.endswith('.gz') else '.bz2'))
    if not destination.exists():
        temporary=destination.with_suffix(destination.suffix+'.partial');size=0;started=time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15,read=120,write=15,pool=15)) as client:
                async with client.stream('GET',url) as response:
                    response.raise_for_status()
                    with temporary.open('wb') as output:
                        async for chunk in response.aiter_bytes():
                            size+=len(chunk)
                            if size>2*1024**3 or time.monotonic()-started>600:raise RuntimeError('ArchiveDownloadBudgetExceeded')
                            output.write(chunk)
            temporary.replace(destination)
        finally:
            if temporary.exists():temporary.unlink()
    def digest():
        h=hashlib.sha256()
        with destination.open('rb') as file:
            for chunk in iter(lambda:file.read(1024**2),b''):h.update(chunk)
        return h.hexdigest()
    return destination,await asyncio.to_thread(digest)

async def import_file(observer,row,file,kind,cache):
    pool=await get_pg_pool()
    existing=await pool.fetchrow('''SELECT records,audit FROM routing_archives
        WHERE observer=$1 AND url=$2 AND generation=$3''',observer['id'],file['url'],row['generation'])
    if existing:return existing['records'],json.loads(existing['audit'])
    path,digest=await download(file['url'],cache)
    review=await asyncio.to_thread(audit,path,observer['peer_ip'])
    if kind=='rib' and not review['selected_peer_present']:raise ValueError('Selected peer absent from snapshot')
    # A session change can implicitly withdraw the entire RIB. UPDATE-only replay
    # cannot reconstruct that safely; require a snapshot after the change.
    changes=[s for s in review['state_changes'] if row['baseline_at'] is None or stamp(s['time'])>row['baseline_at']]
    if kind!='rib' and changes:
        reset=max(stamp(s['time']) for s in changes)
        await pool.execute('''UPDATE routing_observers SET baseline_state='required',baseline_at=NULL,
           reset_at=GREATEST(reset_at,$2),epoch=epoch+1,generation=$3,peer_state='unknown'
           WHERE id=$1 AND epoch=$4 AND generation=$5''',observer['id'],reset,uuid.uuid4(),row['epoch'],row['generation'])
        raise WaitingArchive('Session change requires a newer RIB snapshot')
    import bgpkit
    executor=ThreadPoolExecutor(max_workers=1)
    loop=asyncio.get_running_loop();holder={}
    def create_parser():holder['iterator']=iter(bgpkit.Parser(url=str(path),filters={'peer_ips':observer['peer_ip']}))
    await loop.run_in_executor(executor,create_parser)
    count=0;v4=0;v6=0
    def next_batch():
        nonlocal count,v4,v6
        batch=[]
        for _ in range(1000):
            try:element=next(holder['iterator'])
            except StopIteration:break
            count+=1
            item=mrt_change(element,observer,snapshot=file['at'] if kind=='rib' else None,order=count,url=file['url'])
            if ':' in item['prefix']:v6+=1
            else:v4+=1
            batch.append((count,item))
        return batch
    try:
        while True:
            batch=await loop.run_in_executor(executor,next_batch)
            if not batch:break
            await archive_batch(observer,row['epoch'],row['generation'],batch,0 if kind=='rib' else 1,file['at'].strftime('%Y%m%d%H%M'))
    finally:
        # PyO3 parsers are !Send: construction, iteration and destruction stay on
        # the same dedicated thread. Only plain normalized Python records leave it.
        await loop.run_in_executor(executor,holder.clear)
        executor.shutdown(wait=True)
    if kind=='rib' and (v4!=review['rib_v4'] or v6!=review['rib_v6']):
        raise ValueError('Decoded RIB count does not match independent MRT audit')
    async with pool.acquire() as conn,conn.transaction():
        current=await conn.fetchrow('SELECT * FROM routing_observers WHERE id=$1 FOR UPDATE',observer['id'])
        if current['epoch']!=row['epoch'] or current['generation']!=row['generation']:raise RuntimeError('Observer changed during import')
        await conn.execute('''INSERT INTO routing_archives(observer,url,generation,kind,start_at,until_at,sha256,bytes,records,audit)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb) ON CONFLICT DO NOTHING''',observer['id'],file['url'],row['generation'],kind,
            file['at'],file['until'],digest,path.stat().st_size,count,json.dumps(review))
        if kind=='rib':
            await conn.execute('''UPDATE routing_observers SET baseline_at=$2,baseline_state='snapshot',
                snapshot_v4=$3,snapshot_v6=$4,error_code=NULL WHERE id=$1''',observer['id'],file['at'],v4,v6)
            await conn.execute("UPDATE routing_gaps SET from_at=$2 WHERE observer=$1 AND from_at IS NULL",observer['id'],file['at'])
            await conn.execute('''UPDATE routing_gaps SET state='snapshot_superseded',repaired_at=now()
                WHERE observer=$1 AND until_at<=$2 AND state<>'repaired' ''',observer['id'],file['at'])
    path.unlink() # Only this disposable download; source URL/hash/audit remain durable.
    print('MRT_APPLIED',observer['id'],kind,file['at'].isoformat(),count,flush=True)
    return count,review

async def recover(observer,cache):
    await register(observer)
    pool=await get_pg_pool();now=datetime.now(UTC)
    row=dict(await pool.fetchrow('SELECT * FROM routing_observers WHERE id=$1',observer['id']))
    if row['baseline_state']=='required' or row['baseline_at'] is None:
        snapshots=await manifest(observer,'rib',now-timedelta(hours=30),now-timedelta(minutes=3))
        snapshots=[f for f in snapshots if row['reset_at'] is None or f['at']>=row['reset_at']]
        if not snapshots:raise WaitingArchive('No completed snapshot after the latest session boundary')
        await import_file(observer,row,snapshots[-1],'rib',cache)
        row=dict(await pool.fetchrow('SELECT * FROM routing_observers WHERE id=$1',observer['id']))
    period=300 if observer['mode']=='ris-live' else 900
    gaps=await pool.fetch("SELECT * FROM routing_gaps WHERE observer=$1 AND state NOT IN ('repaired','snapshot_superseded') ORDER BY opened_at",observer['id'])
    closed=[g for g in gaps if g['until_at'] is not None]
    if observer['mode']=='mrt':
        cursor=await pool.fetchval("SELECT max(until_at) FROM routing_archives WHERE observer=$1 AND generation=$2 AND kind='updates'",observer['id'],row['generation'])
        start=cursor or row['baseline_at']
        files=await manifest(observer,'updates',start,now-timedelta(seconds=30))
        files=[f for f in files if f['until']<=now-timedelta(seconds=30)]
        if files:
            # Replay requires every interval, even when later files are available.
            wanted=covering_files(files,start,files[-1]['at'],period)
            for file in wanted:await import_file(observer,row,file,'updates',cache)
            through=wanted[-1]['until']
            await pool.execute("UPDATE routing_observers SET event_at=$2,committed_at=now(),stream_state='archive',baseline_state='reconstructed',error_code=NULL WHERE id=$1 AND epoch=$3",observer['id'],through,row['epoch'])
        elif cursor is None:
            raise WaitingArchive('Snapshot loaded; waiting for the next complete update archive')
    elif closed:
        start=min(max(g['from_at'] or row['baseline_at'],row['baseline_at']) for g in closed)
        until=max(g['until_at'] for g in closed)
        if until>=start:
            files=await manifest(observer,'updates',start-timedelta(seconds=period),until+timedelta(seconds=period))
            wanted=covering_files(files,start,until,period)
            for file in wanted:await import_file(observer,row,file,'updates',cache)
        # Lock the observer before declaring the replay complete. A new live peer
        # reset changes epoch/generation and prevents an obsolete completion.
        async with pool.acquire() as conn,conn.transaction():
            current=await conn.fetchrow('SELECT * FROM routing_observers WHERE id=$1 FOR UPDATE',observer['id'])
            if current['epoch']!=row['epoch'] or current['generation']!=row['generation']:raise WaitingArchive('Session changed during replay')
            await conn.execute("UPDATE routing_gaps SET state='repaired',repaired_at=now(),error_code=NULL WHERE id=ANY($1::uuid[])",[g['id'] for g in closed])
            await conn.execute("UPDATE routing_observers SET baseline_state='reconstructed',error_code=NULL WHERE id=$1",observer['id'])
    elif not gaps and row['baseline_state']=='snapshot' and row['stream_state']=='connected':
        await pool.execute("UPDATE routing_observers SET baseline_state='reconstructed' WHERE id=$1",observer['id'])
    if observer['mode']=='mrt':await publish_archive_status(observer)

async def publish_archive_status(observer):
    pool=await get_pg_pool()
    row=await pool.fetchrow('SELECT * FROM routing_observers WHERE id=$1',observer['id'])
    latest=await pool.fetchrow('SELECT * FROM routing_archives WHERE observer=$1 ORDER BY applied_at DESC LIMIT 1',observer['id'])
    if not latest:return
    detail={'scope':'Selected RouteViews peer, IPv4/IPv6 unicast MRT snapshot and contiguous archive replay',
        'observer':observer,'snapshot_v4':row['snapshot_v4'],'snapshot_v6':row['snapshot_v6'],
        'as_of':row['event_at'].isoformat() if row['event_at'] else None,
        'routing_endpoint':'/api/v1/noc/routing/routes?source=routeviews',
        'limitations':['Archived observations have publication delay, not a real-time feed.',
                      'One selected peer does not establish global health or independent corroboration.']}
    await pool.execute('''INSERT INTO source_status(source,last_attempt,last_success,last_observation,state,records,detail)
        VALUES('routeviews',now(),now(),$1,'partial',$2,$3::jsonb)
        ON CONFLICT(source) DO UPDATE SET last_attempt=now(),last_success=now(),last_observation=excluded.last_observation,
          state='partial',records=excluded.records,detail=excluded.detail,error_code=NULL''',
        row['event_at'] or row['baseline_at'],latest['records'],json.dumps(detail))

async def main(config,cache_dir,once=False):
    observers=json.loads(Path(config).read_text());cache=Path(cache_dir);cache.mkdir(parents=True,exist_ok=True)
    lock=await asyncpg.connect(settings.timescale_dsn)
    if not await lock.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-routing-recovery'))"):raise RuntimeError('Recovery is already running')
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in [signal.SIGTERM,signal.SIGINT]:loop.add_signal_handler(sig,stop.set)
    try:
        while not stop.is_set():
            for observer in observers:
                try:await recover(observer,cache)
                except Exception as exc:
                    print('MRT_RECOVERY_PENDING',observer['id'],type(exc).__name__,str(exc)[:180],flush=True)
                    pool=await get_pg_pool()
                    await pool.execute('UPDATE routing_observers SET error_code=$2 WHERE id=$1',observer['id'],type(exc).__name__)
                if stop.is_set():break
            if once:break
            try:await asyncio.wait_for(stop.wait(),timeout=60)
            except asyncio.TimeoutError:pass
    finally:await lock.close();await close_pg_pool()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--cache',required=True);parser.add_argument('--once',action='store_true')
    args=parser.parse_args();asyncio.run(main(args.config,args.cache,args.once))
