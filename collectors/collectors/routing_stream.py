"""Continuous RIS receiver with bounded queues and transactional checkpoints."""
import argparse,asyncio,json,signal,shutil,time
from datetime import datetime,timezone
from pathlib import Path
import asyncpg,httpx
from collectors.config import settings
from collectors.db import get_pg_pool,close_pg_pool
from collectors.routing_store import register,open_gap,commit_messages
from collectors.routing_stop import request_stop,stop_reason
from collectors.journal_rotation import usage

MAX_FRAME=1024*1024
STOP=None

async def capacity():
    pool=await get_pg_pool()
    async with pool.acquire() as conn:
        policy=await conn.fetchrow('SELECT * FROM routing_journal_policy WHERE id')
        if not policy or not policy['enabled']:raise RuntimeError('JournalRotationNotEnabled')
        if shutil.disk_usage('/').free<policy['root_reserve']:raise RuntimeError('DiskCapacityPause')
        sizes=await usage(conn)
        if sizes['hot_bytes']>policy['hot_limit']:raise RuntimeError('JournalCapacityPause')
        if sizes['key_bytes']>policy['key_limit']:raise RuntimeError('DedupCapacityPause')
        if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM routing_journal_segments WHERE from_at<=now() AND until_at>now() AND retired_at IS NULL AND name LIKE 'routing_messages_p%')"):
            raise RuntimeError('JournalPartitionUnavailable')

async def receive(observer,queue,stop):
    reason='Disconnected'
    try:
        subscription={'host':observer['collector'],'peer':observer['peer_ip'],
                      'socketOptions':{'includeRaw':True}}
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15,read=120,write=15,pool=15)) as client:
            async with client.stream('GET','https://ris-live.ripe.net/v1/stream/',
                params={'format':'json','client':'inet-health-engine-routing/1.0'},
                headers={'X-RIS-Subscribe':json.dumps(subscription)}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():continue
                    if len(line.encode())>MAX_FRAME:raise ValueError('Frame exceeds bounded input size')
                    raw=json.loads(line)
                    if raw.get('type')=='ris_error':raise RuntimeError('ProviderStreamError')
                    if raw.get('type')!='ris_message':continue
                    await queue.put((raw,datetime.now(timezone.utc)))
                    if stop.is_set():reason=stop_reason(stop);break
    except Exception as exc:
        reason=type(exc).__name__
    finally:
        # The consumer processes every queued frame before recording this gap.
        await queue.put((None,reason))

async def run_observer(observer,stop):
    await register(observer);await open_gap(observer['id'],'ReceiverStart')
    while not stop.is_set():
        queue=asyncio.Queue(maxsize=64)
        reader=asyncio.create_task(receive(observer,queue,stop))
        batch=[];batch_bytes=0;checked=0
        reason='Disconnected'
        while True:
            try:item=await asyncio.wait_for(queue.get(),timeout=1)
            except asyncio.TimeoutError:item='flush'
            if item!='flush' and item[0] is None:
                reason=stop_reason(stop,item[1])
                if batch:
                    await persist(observer,batch);batch=[]
                break
            if item!='flush':
                batch.append(item);batch_bytes+=len(json.dumps(item[0]))
            if batch and (item=='flush' or len(batch)>=64 or batch_bytes>=2*1024**2):
                await persist(observer,batch);batch=[];batch_bytes=0
            if time.monotonic()-checked>60:
                try:await capacity()
                except RuntimeError as exc:
                    request_stop(stop,str(exc));reason=stop_reason(stop)
                checked=time.monotonic()
        await reader
        await open_gap(observer['id'],reason)
        print('ROUTING_CONNECTION_END',observer['id'],reason,flush=True)
        if not stop.is_set():
            try:await asyncio.wait_for(stop.wait(),timeout=5)
            except asyncio.TimeoutError:pass
    pool=await get_pg_pool()
    await pool.execute("UPDATE routing_observers SET stream_state='stopped' WHERE id=$1",observer['id'])

async def persist(observer,batch):
    # Retain an uncommitted batch during database failure; backpressure is bounded.
    delay=1
    while True:
        try:
            await commit_messages(observer,batch);return
        except (asyncpg.PostgresError,OSError,asyncio.TimeoutError) as exc:
            print('ROUTING_DB_RETRY',observer['id'],type(exc).__name__,flush=True)
            await asyncio.sleep(delay);delay=min(delay*2,30)
        # Malformed frames fail the service; the checkpoint remains behind them
        # and the next start exposes an unreconciled gap instead of skipping data.

async def main(config):
    global STOP
    STOP=asyncio.Event()
    loop=asyncio.get_running_loop()
    for sig in [signal.SIGTERM,signal.SIGINT]:loop.add_signal_handler(sig,request_stop,STOP,'SignalStop')
    observers=json.loads(Path(config).read_text())
    if len(observers)>6 or sum(o['mode']=='ris-live' for o in observers)>4:raise ValueError('Review capacity before enabling more than four RIS or six total observers')
    await capacity()
    lock=await asyncpg.connect(settings.timescale_dsn)
    if not await lock.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-routing-stream'))"):
        raise RuntimeError('Another routing receiver owns this database')
    try:await asyncio.gather(*(run_observer(o,STOP) for o in observers if o['mode']=='ris-live'))
    finally:
        await lock.close();await close_pg_pool()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True)
    asyncio.run(main(parser.parse_args().config))
