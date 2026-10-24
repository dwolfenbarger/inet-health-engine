"""Bounded hourly journal partitions, global dedup identities and verified retirement."""
import json,re
from datetime import datetime,timedelta,timezone
UTC=timezone.utc
NAME=re.compile(r'routing_messages_(?:p[0-9]{10}|bootstrap)\Z')
def checked_name(name):
    if not NAME.fullmatch(name):raise ValueError('Invalid journal segment')
    return name

def hour(at):return at.astimezone(UTC).replace(minute=0,second=0,microsecond=0)

async def prepare(conn,now=None):
    """Admin-only bootstrap. Caller must hold the stopped receiver's advisory lock."""
    now=now or datetime.now(UTC)
    kind=await conn.fetchval("SELECT relkind::text FROM pg_class WHERE oid='public.routing_messages'::regclass")
    if kind=='r':
        # Durable pages retain progress when a storage query times out. The
        # receiver advisory lock is held; payloads and checkpoints are unchanged.
        for observer in await conn.fetch('SELECT id FROM routing_observers ORDER BY id'):
            cursor=await conn.fetchval('SELECT message_id FROM routing_journal_copy_progress WHERE observer=$1',observer['id']) or ''
            while True:
                async with conn.transaction():
                    progress=await conn.fetchrow('''WITH candidates AS MATERIALIZED (
                      SELECT observer,message_id,received_at,event_at,sequence FROM routing_messages
                      WHERE observer=$1 AND message_id>$2 ORDER BY message_id LIMIT 10000
                    ), inserted AS (
                      INSERT INTO routing_message_keys(observer,message_id,received_at,event_at,sequence)
                      SELECT * FROM candidates ON CONFLICT DO NOTHING RETURNING message_id)
                    SELECT max(message_id) AS last,count(*) AS records FROM candidates''',observer['id'],cursor)
                    if not progress['records']:break
                    cursor=progress['last']
                    await conn.execute('''INSERT INTO routing_journal_copy_progress(observer,message_id) VALUES($1,$2)
                      ON CONFLICT(observer) DO UPDATE SET message_id=excluded.message_id,copied_at=now()''',observer['id'],cursor)
                print('JOURNAL_KEY_PAGE',observer['id'],cursor,flush=True)
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='3s'")
            await conn.execute('LOCK TABLE routing_messages IN ACCESS EXCLUSIVE MODE')
            missing=await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM routing_messages m
              LEFT JOIN routing_message_keys k ON k.observer=m.observer AND k.message_id=m.message_id
              WHERE k.message_id IS NULL)''')
            if missing:raise RuntimeError('Bootstrap identities do not match source journal')
            mismatch=await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM routing_observers o
              LEFT JOIN (SELECT observer,count(*) AS records FROM routing_message_keys GROUP BY observer) k ON k.observer=o.id
              WHERE o.mode='ris-live' AND coalesce(k.records,0)<>o.messages)''')
            if mismatch:raise RuntimeError('Bootstrap identity count does not match checkpoints')
            # Use the existing observer/time index rather than scanning payload pages.
            extent=await conn.fetchrow('''SELECT min(a.received_at) AS first,max(z.received_at) AS last
              FROM routing_observers o CROSS JOIN LATERAL (SELECT received_at FROM routing_messages
               WHERE observer=o.id ORDER BY received_at ASC LIMIT 1) a
              CROSS JOIN LATERAL (SELECT received_at FROM routing_messages
               WHERE observer=o.id ORDER BY received_at DESC LIMIT 1) z''')
            await conn.execute('ALTER TABLE routing_messages RENAME TO routing_messages_bootstrap')
            for row in await conn.fetch("SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='routing_messages_bootstrap'"):
                name=row['indexname']
                if not re.fullmatch('[a-z_]+',name):raise ValueError('Unexpected legacy index name')
                await conn.execute(f'ALTER INDEX "{name}" RENAME TO "{name}_bootstrap"')
            await conn.execute('''CREATE TABLE routing_messages (
              observer text NOT NULL REFERENCES routing_observers(id),message_id text NOT NULL,
              received_at timestamptz NOT NULL,event_at timestamptz NOT NULL,sequence bigint NOT NULL,payload jsonb NOT NULL,
              stored_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(observer,message_id,stored_at)
            ) PARTITION BY RANGE(stored_at)''')
            await conn.execute('CREATE INDEX routing_messages_recent ON routing_messages(observer,received_at DESC)')
            if extent['first']:
                await conn.execute('INSERT INTO routing_journal_segments(name,from_at,until_at) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',
                  'routing_messages_bootstrap',extent['first'],extent['last']+timedelta(microseconds=1))
            await conn.execute('GRANT SELECT ON routing_messages TO inetuser')
            # The application cannot backdate the partition clock or mutate payloads.
            await conn.execute('GRANT INSERT(observer,message_id,received_at,event_at,sequence,payload) ON routing_messages TO inetuser')
    elif kind!='p':raise RuntimeError('Unsupported journal relation')
    await partitions(conn,now)

async def partitions(conn,now=None):
    start=hour(now or datetime.now(UTC))-timedelta(hours=1)
    async with conn.transaction():
        await conn.execute("SET LOCAL lock_timeout='3s'")
        for i in range(50):
            at=start+timedelta(hours=i);until=at+timedelta(hours=1);name=checked_name('routing_messages_p'+at.strftime('%Y%m%d%H'))
            await conn.execute(f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF routing_messages FOR VALUES FROM ('{at.isoformat()}') TO ('{until.isoformat()}')")
            await conn.execute(f'GRANT SELECT ON {name} TO inetuser')
            await conn.execute('INSERT INTO routing_journal_segments(name,from_at,until_at) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',name,at,until)

async def usage(conn):
    return dict(await conn.fetchrow('''SELECT
      coalesce((SELECT sum(pg_total_relation_size(to_regclass(s.name))) FROM routing_journal_segments s WHERE retired_at IS NULL),0)::bigint AS hot_bytes,
      pg_total_relation_size('routing_message_keys') AS key_bytes,
      (SELECT count(*) FROM routing_journal_segments WHERE until_at<now()-interval '24 hours' AND retired_at IS NULL) AS due_segments'''))

async def retire(conn,name,receipt,now=None):
    """Retire only a complete, NAS-readback verified, aged immutable segment."""
    checked_name(name);now=now or datetime.now(UTC)
    if receipt.get('verified') is not True or receipt.get('local_retirement_eligible') is not True or receipt.get('segment')!=name or not re.fullmatch('[a-f0-9]{64}',receipt.get('manifest_sha256','')):
        raise ValueError('Verified matching archive receipt required')
    if receipt.get('storage_id')!='e6a0939db1dd4a12a5dbec00ccf45296' or receipt.get('environment')!='development':
        raise ValueError('Archive storage identity differs')
    async with conn.transaction():
        await conn.execute("SET LOCAL lock_timeout='3s'")
        segment=await conn.fetchrow('SELECT * FROM routing_journal_segments WHERE name=$1 FOR UPDATE',name)
        policy=await conn.fetchrow('SELECT * FROM routing_journal_policy WHERE id')
        if not policy['enabled']:raise RuntimeError('Rotation policy disabled')
        if segment['retired_at'] is not None:return False
        if segment['until_at']>now-timedelta(hours=policy['local_hours']):raise ValueError('Local detail window is protected')
        await conn.execute(f'LOCK TABLE {name} IN ACCESS EXCLUSIVE MODE')
        actual=[dict(r) for r in await conn.fetch(f'SELECT observer,count(*) AS records,min(sequence) AS first,max(sequence) AS last FROM {name} GROUP BY observer ORDER BY observer')]
        if actual!=receipt.get('observers'):raise ValueError('Segment changed after export')
        # Parent keys remain durable across payload rotation and prevent double application.
        await conn.execute(f'DROP TABLE {name}')
        await conn.execute("UPDATE routing_journal_segments SET state='retired',receipt=$2::jsonb,archived_at=coalesce(archived_at,now()),retired_at=now() WHERE name=$1",name,json.dumps(receipt))
    return True

async def trim_keys(conn,now=None):
    """Expired identities have an explicit event-time floor; late frames become gaps."""
    now=now or datetime.now(UTC)
    policy=await conn.fetchrow('SELECT * FROM routing_journal_policy WHERE id')
    if not policy['enabled']:return 0
    floor=now-timedelta(hours=policy['dedup_hours'])
    async with conn.transaction():
        await conn.execute('UPDATE routing_observers SET dedup_floor_at=GREATEST(dedup_floor_at,$1) WHERE mode=\'ris-live\'',floor)
        # Keep identities whose payload still needs archiving, including late ingestion.
        result=await conn.execute('''DELETE FROM routing_message_keys WHERE ctid IN (
          SELECT k.ctid FROM routing_message_keys k WHERE event_at<$1 AND stored_at<$1
          AND NOT EXISTS(SELECT 1 FROM routing_journal_segments s WHERE s.retired_at IS NULL AND s.from_at<=k.stored_at AND s.until_at>k.stored_at)
          LIMIT 10000)''',floor)
    return int(result.split()[-1])
