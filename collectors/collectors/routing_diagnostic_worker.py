"""Independent, restartable continuous-journal diagnostic consumer.

Only selected RIS observer journals are covered. New installations begin at the
committed head; they do not infer prior history from today's RIB. Source intake
is never restarted or blocked by this worker.
"""
import argparse,asyncio,json,signal,shutil,uuid,time
from datetime import datetime,timezone,timedelta
from collectors.routing_model import ris_changes
from collectors.routing_diagnostics import apply_change,apply_withdrawal

PAGE_TTL = 300
MAX_BATCH_CHANGES = 16384
class DiagnosticInputError(ValueError):
    """A public reason code; raw input is retained and the cursor is not advanced."""

def decode(v):return json.loads(v) if isinstance(v,str) else v

def participants(value):
    if isinstance(value,int):return {value} if value>0 else set()
    if isinstance(value,list):return set().union(*(participants(v) for v in value)) if value else set()
    return set()

async def record(conn,observer,frame,epoch,ordinal,prefix,event):
    before=event.get('before') or {};after=event.get('after') or {};facts=event.get('facts') or {}
    origin=after.get('origin') or before.get('origin')
    expected=facts.get('expected_asn') or (before.get('origin') if before.get('origin')!=origin else None)
    asns=participants(before.get('path'))|participants(after.get('path'))|participants(facts.get('previous_announced_path'))|{observer['peer_asn']}
    asns|=participants([p['as_path'] for p in facts.get('withdrawn_prefix_paths',[])])
    asns|=participants(origin)|participants(expected)
    evidence={**event,'collector':observer['collector'],'peer_asn':observer['peer_asn'],
              'peer_ip':str(observer['peer_ip']),'message_id':frame['message_id'],
              'event_at':frame['event_at'].isoformat(),'received_at':frame['received_at'].isoformat()}
    ident=uuid.uuid5(uuid.NAMESPACE_URL,f"inet-diag-v1:{observer['id']}:{frame['sequence']}:{ordinal}:{event['kind']}")
    conn.append((ident,observer['id'],frame['sequence'],ordinal,epoch,
      frame['event_at'],frame['received_at'],prefix,event['kind'],event.get('candidate'),origin,expected,sorted(asns),json.dumps(evidence)))


async def process_frames(conn,observer,cursor,frames,gaps):
    """Caller owns the transaction and cursor lock; never advances on failure."""
    cp=dict(cursor);cp['continuity']=decode(cp.get('continuity') or {});states={};origins={};dirty=set();dirty_origins=set();events_out=[]
    changes=[]
    change_count=0
    for frame in frames:
        if frame['sequence']<=cursor['sequence']:continue
        raw=decode(frame['payload']);at,ident,items=ris_changes(raw,observer)
        if ident!=frame['message_id']:raise DiagnosticInputError('JournalIdentityMismatch')
        if at!=frame['event_at']:raise DiagnosticInputError('JournalTimeMismatch')
        if changes and change_count+len(items)>MAX_BATCH_CHANGES:break
        if len(items)>MAX_BATCH_CHANGES:raise DiagnosticInputError('FrameCapacityExceeded')
        change_count+=len(items)
        changes.append((frame,raw,items))
    prefixes=list({item['prefix'] for _,_,items in changes for item in items})
    if prefixes:
        rows=await conn.fetch('SELECT prefix::text prefix,state FROM routing_diagnostic_state WHERE observer=$1 AND prefix=ANY($2::text[]::cidr[])',observer['id'],prefixes)
        states={r['prefix']:decode(r['state']) for r in rows}
    for frame,raw,items in changes:
        if frame['sequence']<=cp['sequence']:continue
        reasons=[]
        if frame['sequence']!=cp['sequence']+1:reasons.append('JournalSequenceGap')
        for gap in gaps:
            if cp['gap_seen']<gap['opened_at']<=frame['received_at']:
                reasons.append('SourceGap:'+gap['reason']);cp['gap_seen']=gap['opened_at']
        at=frame['event_at'];kind=raw['data']['type']
        # Late session controls cannot reset newer event-time state.
        if (kind in ['OPEN','NOTIFICATION'] or (kind=='RIS_PEER_STATE' and raw['data'].get('state')=='down')) and (not cp['event_at'] or at>=cp['event_at']):
            reasons.append('PeerSessionReset')
        if reasons:
            cp['epoch']+=1
            cp['continuity']={'reasons':reasons,'sequence':frame['sequence'],'event_at':frame['event_at'].isoformat(),'received_at':frame['received_at'].isoformat()}
            await record(events_out,observer,frame,cp['epoch'],-1,None,{'kind':'continuity_reset','facts':{'reasons':reasons,'prior_sequence':cp['sequence'],'next_sequence':frame['sequence']}})
        if at>datetime.now(timezone.utc)+timedelta(minutes=5):raise DiagnosticInputError('FutureEventTime')
        for i,item in enumerate(items):
            prefix=item['prefix'];prior=states.get(prefix)
            state,events,accepted=apply_change(prior,item,cp['epoch'])
            if not accepted:cp['skipped_late']+=1;continue
            states[prefix]=state;dirty.add(prefix)
            for event in events:
                if event['kind']=='baseline':event['facts']['source_continuity']=cp['continuity']
                await record(events_out,observer,frame,cp['epoch'],i,prefix,event)
            if prior and prior['epoch']==cp['epoch'] and prior['present'] and not state['present'] and prior.get('origin') and at.timestamp()-prior['at']<=86400:
                asn=prior['origin']
                if asn not in origins:
                    row=await conn.fetchrow('SELECT state FROM routing_diagnostic_origins WHERE observer=$1 AND asn=$2',observer['id'],asn)
                    origins[asn]=decode(row['state']) if row else None
                origins[asn],surge=apply_withdrawal(origins[asn],prefix,at.timestamp(),cp['epoch'],before_path=prior['path']);dirty_origins.add(asn)
                if surge:
                    await record(events_out,observer,frame,cp['epoch'],i,prefix,{'kind':'withdrawal_threshold','candidate':'withdrawal_surge',
                        'before':{'present':True,'origin':asn,'path':prior['path']},'after':{'present':False,'origin':None,'path':[]},'facts':surge})
        cp['sequence']=frame['sequence'];cp['received_at']=frame['received_at']
        cp['event_at']=max(cp['event_at'] or at,at);cp['processed']+=1
    if events_out:
        await conn.executemany('''INSERT INTO routing_diagnostic_events(event_id,observer,sequence,ordinal,epoch,
      event_at,received_at,prefix,kind,candidate,origin_asn,expected_asn,involved_asns,evidence)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8::text::cidr,$9,$10,$11,$12,$13,$14::jsonb)
      ON CONFLICT(event_id,event_at) DO NOTHING''',events_out)
    if dirty:
        await conn.executemany('''INSERT INTO routing_diagnostic_state(observer,prefix,observed_at,state)
          VALUES($1,$2::text::cidr,$3,$4::jsonb) ON CONFLICT(observer,prefix) DO UPDATE SET observed_at=excluded.observed_at,state=excluded.state''',
          [(observer['id'],p,datetime.fromtimestamp(states[p]['at'],timezone.utc),json.dumps(states[p])) for p in dirty])
    if dirty_origins:
        await conn.executemany('''INSERT INTO routing_diagnostic_origins(observer,asn,observed_at,state) VALUES($1,$2,$3,$4::jsonb)
          ON CONFLICT(observer,asn) DO UPDATE SET observed_at=excluded.observed_at,state=excluded.state''',
          [(observer['id'],a,datetime.fromtimestamp(origins[a]['at'],timezone.utc),json.dumps(origins[a])) for a in dirty_origins])
    await conn.execute('''UPDATE routing_diagnostic_cursors SET sequence=$2,epoch=$3,received_at=$4,event_at=$5,gap_seen=$6,
       processed=$7,skipped_late=$8,continuity=$9::jsonb,heartbeat_at=now(),status='processing',error_code=NULL WHERE observer=$1''',
       observer['id'],cp['sequence'],cp['epoch'],cp['received_at'],cp['event_at'],cp['gap_seen'],cp['processed'],cp['skipped_late'],json.dumps(cp['continuity']))
    return cp

async def cycle(pool,observer):
    async with pool.acquire() as conn,conn.transaction():
        await conn.execute("SET LOCAL lock_timeout='200ms'")
        await conn.execute("SET LOCAL statement_timeout='5s'")
        # Serialize only the diagnostic checkpoint. Never lock the collector's observer row.
        cp=await conn.fetchrow('SELECT * FROM routing_diagnostic_cursors WHERE observer=$1 FOR UPDATE',observer['id'])
        if not cp:
            latest=await conn.fetchrow('SELECT sequence,received_at,event_at FROM routing_observers WHERE id=$1',observer['id'])
            now=datetime.now(timezone.utc)
            await conn.execute('''INSERT INTO routing_diagnostic_cursors(observer,sequence,received_at,event_at,gap_seen,heartbeat_at)
              VALUES($1,$2,$3,$4,$5,now())''',observer['id'],latest['sequence'],latest['received_at'] or now,latest['event_at'],now)
            return 0
        frames=await conn.fetch('''SELECT message_id,sequence,event_at,received_at,payload FROM routing_messages
          WHERE observer=$1 AND sequence>$2 ORDER BY sequence LIMIT 128''',observer['id'],cp['sequence'])
        gaps=await conn.fetch('SELECT opened_at,reason FROM routing_gaps WHERE observer=$1 AND opened_at>$2 ORDER BY opened_at',observer['id'],cp['gap_seen'])
        if not frames:
            head=await conn.fetchval('SELECT sequence FROM routing_observers WHERE id=$1',observer['id'])
            if head>cp['sequence']:
                await conn.execute("UPDATE routing_diagnostic_cursors SET heartbeat_at=now(),status='error',error_code='JournalNotRetained' WHERE observer=$1",observer['id'])
                return 0
            await conn.execute("UPDATE routing_diagnostic_cursors SET heartbeat_at=now(),status='waiting',error_code=NULL WHERE observer=$1",observer['id'])
            return 0
        await process_frames(conn,observer,cp,frames,gaps)
        return len(frames)

async def maintenance(pool):
    policy=await pool.fetchrow('SELECT * FROM routing_diagnostic_policy WHERE id')
    if not policy or not policy['enabled']:return False
    # Bounded batches keep expiry from monopolizing the database.
    # The page TTL is absolute from the first page. This grace protects every
    # row within a pinned page's effective retention window until it expires.
    partitioned=await pool.fetchval("SELECT relkind='p' FROM pg_class WHERE oid='public.routing_diagnostic_events'::regclass")
    expiry=[('routing_diagnostic_state','observed_at',86400),('routing_diagnostic_origins','observed_at',600)]
    if not partitioned:expiry.insert(0,('routing_diagnostic_events','event_at',policy['retention_hours']*3600+PAGE_TTL))
    for table,col,seconds in expiry:
        await pool.execute(f'DELETE FROM {table} WHERE ctid IN (SELECT ctid FROM {table} WHERE {col}<now()-make_interval(secs=>$1) LIMIT 10000)',float(seconds))
    size=await pool.fetchval("""WITH RECURSIVE owned(oid) AS (
      SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace AND relname=ANY($1::text[])
      UNION ALL SELECT i.inhrelid FROM pg_inherits i JOIN owned o ON o.oid=i.inhparent)
      SELECT coalesce(sum(pg_total_relation_size(c.oid)),0)::bigint FROM owned JOIN pg_class c USING(oid) WHERE c.relkind='r'""",['routing_diagnostic_events','routing_diagnostic_state','routing_diagnostic_origins','routing_diagnostic_cursors'])
    ready=size<policy['max_bytes'] and shutil.disk_usage('/').free>policy['root_reserve']
    await pool.execute("UPDATE routing_diagnostic_policy SET state=$1,checked_at=now(),measured_bytes=$2 WHERE id",'running' if ready else 'capacity_paused',size)
    return ready

async def main(observers=None):
    from collectors.db import get_pg_pool,close_pg_pool
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in [signal.SIGINT,signal.SIGTERM]:loop.add_signal_handler(sig,stop.set)
    pool=await get_pg_pool();lock=await pool.acquire()
    if not await lock.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-routing-diagnostics'))"):
        raise RuntimeError('Diagnostic worker already running')
    checked=0;ready=False
    try:
        while not stop.is_set():
            try:
                if time.monotonic()-checked>30:
                    ready=await maintenance(pool);checked=time.monotonic()
                count=0
                if ready:
                    for row in await pool.fetch("SELECT id,collector,peer_ip,peer_asn FROM routing_observers WHERE mode='ris-live' AND ($1::text[] IS NULL OR id=ANY($1)) ORDER BY id",observers):
                        if stop.is_set():break
                        try:count+=await cycle(pool,dict(row))
                        except Exception as exc:
                            code=str(exc) if isinstance(exc,DiagnosticInputError) else type(exc).__name__
                            await pool.execute("UPDATE routing_diagnostic_cursors SET status='error',error_code=$2,heartbeat_at=now() WHERE observer=$1",row['id'],code)
                            print('DIAGNOSTIC_ERROR',row['id'],code,flush=True)
                if count==0:
                    try:await asyncio.wait_for(stop.wait(),timeout=2)
                    except asyncio.TimeoutError:pass
                else:await asyncio.sleep(.05)
            except Exception as exc:
                print('DIAGNOSTIC_LOOP_ERROR',type(exc).__name__,flush=True)
                try:await asyncio.wait_for(stop.wait(),timeout=5)
                except asyncio.TimeoutError:pass
    finally:
        await pool.release(lock);await close_pg_pool()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--observer',action='append')
    asyncio.run(main(parser.parse_args().observer))
