"""Atomic journal, route state and checkpoint commits; additive tables only."""
import json,uuid
from datetime import datetime,timezone
from collectors.db import get_pg_pool
from collectors.routing_model import ris_changes

UPSERT='''INSERT INTO routing_routes
(observer,prefix,epoch,generation,event_at,rank,event_order,present,origin_asn,path,attrs,evidence_id)
VALUES($1,$2::text::cidr,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12)
ON CONFLICT(observer,prefix) DO UPDATE SET
 epoch=excluded.epoch,generation=excluded.generation,
 event_at=CASE WHEN (excluded.event_at,excluded.rank,excluded.event_order)>(routing_routes.event_at,routing_routes.rank,routing_routes.event_order)
                   OR excluded.epoch<>routing_routes.epoch THEN excluded.event_at ELSE routing_routes.event_at END,
 rank=CASE WHEN (excluded.event_at,excluded.rank,excluded.event_order)>(routing_routes.event_at,routing_routes.rank,routing_routes.event_order)
                   OR excluded.epoch<>routing_routes.epoch THEN excluded.rank ELSE routing_routes.rank END,
 event_order=CASE WHEN (excluded.event_at,excluded.rank,excluded.event_order)>(routing_routes.event_at,routing_routes.rank,routing_routes.event_order)
                   OR excluded.epoch<>routing_routes.epoch THEN excluded.event_order ELSE routing_routes.event_order END,
 present=CASE WHEN (excluded.event_at,excluded.rank,excluded.event_order)>(routing_routes.event_at,routing_routes.rank,routing_routes.event_order)
                   OR excluded.epoch<>routing_routes.epoch THEN excluded.present ELSE routing_routes.present END,
 origin_asn=CASE WHEN (excluded.event_at,excluded.rank,excluded.event_order)>(routing_routes.event_at,routing_routes.rank,routing_routes.event_order)
                   OR excluded.epoch<>routing_routes.epoch THEN excluded.origin_asn ELSE routing_routes.origin_asn END,
 path=CASE WHEN (excluded.event_at,excluded.rank,excluded.event_order)>(routing_routes.event_at,routing_routes.rank,routing_routes.event_order)
                   OR excluded.epoch<>routing_routes.epoch THEN excluded.path ELSE routing_routes.path END,
 attrs=CASE WHEN (excluded.event_at,excluded.rank,excluded.event_order)>(routing_routes.event_at,routing_routes.rank,routing_routes.event_order)
                   OR excluded.epoch<>routing_routes.epoch THEN excluded.attrs ELSE routing_routes.attrs END,
 evidence_id=CASE WHEN (excluded.event_at,excluded.rank,excluded.event_order)>(routing_routes.event_at,routing_routes.rank,routing_routes.event_order)
                   OR excluded.epoch<>routing_routes.epoch THEN excluded.evidence_id ELSE routing_routes.evidence_id END
WHERE excluded.epoch>routing_routes.epoch OR (excluded.epoch=routing_routes.epoch AND
 (excluded.event_at,excluded.rank,excluded.event_order)>(routing_routes.event_at,routing_routes.rank,routing_routes.event_order))
'''

def route_row(observer,row,change,rank,order):
    return (observer,row['prefix'],change['epoch'],change['generation'],row['event_at'],rank,order,
            row['present'],row['origin_asn'],json.dumps(row['path']),json.dumps(row['attrs']),row['evidence_id'])

def consolidate_routes(rows):
    """Keep the SQL winner per observer/prefix inside one atomic transaction.

    Journal frames and detector inputs are unchanged. This is only a current-view
    write reduction; equal ordering keys retain the first row, as UPSERT does.
    """
    winners = {}
    for value in rows:
        key = value[:2]
        previous = winners.get(key)
        order = (value[2], value[4], value[5], value[6])
        if previous is None or order > (previous[2], previous[4], previous[5], previous[6]):
            winners[key] = value
    return list(winners.values())

async def register(observer):
    pool=await get_pg_pool()
    async with pool.acquire() as conn:
        await conn.execute('''INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation)
            VALUES($1,$2,$3,$4::text::inet,$5,$6,$7::jsonb,$8) ON CONFLICT(id) DO NOTHING''',
            observer['id'],observer['source'],observer['collector'],observer['peer_ip'],observer['peer_asn'],observer['mode'],json.dumps(observer),uuid.uuid4())
        row=await conn.fetchrow('SELECT * FROM routing_observers WHERE id=$1',observer['id'])
        # Configuration changes must be a deliberate new observer identity.
        if any(str(row[k])!=str(observer[k]) for k in ['source','collector','peer_ip','peer_asn','mode']):
            raise ValueError('Existing observer identity differs')

async def open_gap(observer,reason):
    pool=await get_pg_pool()
    async with pool.acquire() as conn,conn.transaction():
        row=await conn.fetchrow('SELECT * FROM routing_observers WHERE id=$1 FOR UPDATE',observer)
        await conn.execute('''INSERT INTO routing_gaps(id,observer,from_at,reason)
           SELECT $1,$2,$3,$4 WHERE NOT EXISTS(SELECT 1 FROM routing_gaps WHERE observer=$2 AND until_at IS NULL)''',
           uuid.uuid4(),observer,row['event_at'],reason)
        await conn.execute("UPDATE routing_observers SET stream_state=$2,reconnects=reconnects+1,error_code=$3 WHERE id=$1",
                           observer,'disconnected',reason)

async def commit_messages(observer,frames):
    if not frames:return
    pool=await get_pg_pool()
    async with pool.acquire() as conn,conn.transaction():
        row=dict(await conn.fetchrow('SELECT * FROM routing_observers WHERE id=$1 FOR UPDATE',observer['id']))
        pending_routes=[]
        for raw,received in frames:
            at,ident,changes=ris_changes(raw,observer)
            seq=row['sequence']+1
            inserted=await conn.fetchval('''INSERT INTO routing_message_keys(observer,message_id,received_at,event_at,sequence)
                VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING RETURNING sequence''',
                observer['id'],ident,received,at,seq)
            if inserted is not None:
                await conn.execute('''INSERT INTO routing_messages(observer,message_id,received_at,event_at,sequence,payload)
                    VALUES($1,$2,$3,$4,$5,$6::jsonb)''',observer['id'],ident,received,at,seq,json.dumps(raw))
            if inserted is None:continue
            row['sequence']=seq;row['messages']+=1;row['updates']+=len(changes)
            if row.get('dedup_floor_at') is not None and at<row['dedup_floor_at']:
                # The bounded dedup horizon is explicit. Preserve the frame for
                # audit, but an old OPEN/UPDATE cannot reset or mutate current state.
                await conn.execute('''INSERT INTO routing_gaps(id,observer,from_at,until_at,reason,state)
                    VALUES($1,$2,$3,$4,'LateBeyondDedupHorizon','waiting_archive')''',uuid.uuid4(),observer['id'],at,row['event_at'] or at)
                continue
            data=raw['data'];kind=data['type']
            if kind in ['OPEN','NOTIFICATION'] or (kind=='RIS_PEER_STATE' and data.get('state')=='down' and row['peer_state']!='down'):
                row['epoch']+=1;row['generation']=uuid.uuid4();row['baseline_state']='required';row['reset_at']=at
            if kind=='RIS_PEER_STATE':row['peer_state']=data.get('state','unknown')
            elif kind in ['KEEPALIVE','UPDATE'] and row['peer_state']!='down':row['peer_state']='observed'
            if row['reset_at'] is not None and at<row['reset_at']:changes=[]
            pending_routes.extend(route_row(observer['id'],item,row,2,f'{seq:020d}:{i:06d}') for i,item in enumerate(changes))
            await conn.execute('''UPDATE routing_gaps SET until_at=$2,state='waiting_archive'
                WHERE observer=$1 AND until_at IS NULL AND (from_at IS NULL OR from_at<=$2)''',observer['id'],at)
            row['event_at']=max(row['event_at'] or at,at);row['received_at']=received
        if pending_routes:
            await conn.executemany(UPSERT,consolidate_routes(pending_routes))
        await conn.execute('''UPDATE routing_observers SET epoch=$2,generation=$3,sequence=$4,messages=$5,updates=$6,
            peer_state=$7,baseline_state=$8,reset_at=$9,event_at=$10,received_at=$11,
            committed_at=now(),stream_state='connected',error_code=NULL,
            baseline_at=CASE WHEN epoch<>$2 THEN NULL ELSE baseline_at END,
            snapshot_v4=CASE WHEN epoch<>$2 THEN 0 ELSE snapshot_v4 END,
            snapshot_v6=CASE WHEN epoch<>$2 THEN 0 ELSE snapshot_v6 END WHERE id=$1''',
            observer['id'],row['epoch'],row['generation'],row['sequence'],row['messages'],row['updates'],row['peer_state'],
            row['baseline_state'],row['reset_at'],row['event_at'],row['received_at'])

async def archive_batch(observer,epoch,generation,changes,rank,tag):
    pool=await get_pg_pool()
    async with pool.acquire() as conn,conn.transaction():
        row=await conn.fetchrow('SELECT * FROM routing_observers WHERE id=$1 FOR UPDATE',observer['id'])
        if row['epoch']!=epoch or row['generation']!=generation:raise RuntimeError('Observer session changed during archive import')
        await conn.executemany(UPSERT,consolidate_routes(route_row(observer['id'],item,row,rank,f'{tag}:{i:020d}') for i,item in changes))

async def finalize_snapshot(conn,observer,epoch,generation,at,url):
    """A complete snapshot supersedes pre-snapshot live entries absent from it.

    Imported entries carry the snapshot timestamp. Newer live entries must win;
    an absent entry is snapshot evidence, not an invented BGP withdrawal.
    Caller holds the observer lock after independently auditing the full RIB.
    """
    result=await conn.execute('''UPDATE routing_routes SET present=false,origin_asn=NULL,path='[]'::jsonb,
        event_at=$4,rank=0,event_order='snapshot-absence',
        attrs=jsonb_build_object('snapshot_absence',true,'snapshot_url',$5::text),
        evidence_id=$5||'#snapshot-absence:'||prefix::text
        WHERE observer=$1 AND epoch=$2 AND generation=$3 AND present AND event_at<$4''',
        observer,epoch,generation,at,url)
    # A checkpoint later than the RIB must be covered by archives even after
    # a session reset invalidated live entries from an earlier generation.
    row=await conn.fetchrow('SELECT event_at,mode FROM routing_observers WHERE id=$1',observer)
    if row['mode']=='ris-live' and row['event_at'] and row['event_at']>at:
        gap_id=uuid.uuid5(uuid.NAMESPACE_URL,f'{observer}:{generation}:{at.isoformat()}:snapshot-catchup')
        await conn.execute('''INSERT INTO routing_gaps(id,observer,from_at,until_at,reason,state)
            VALUES($1,$2,$3,$4,'SnapshotCatchup','waiting_archive') ON CONFLICT(id) DO NOTHING''',
            gap_id,observer,at,row['event_at'])
        await conn.execute('''UPDATE routing_observers SET baseline_state='snapshot' WHERE id=$1
            AND EXISTS(SELECT 1 FROM routing_gaps WHERE id=$2 AND state NOT IN ('repaired','snapshot_superseded'))''',observer,gap_id)
    return int(result.split()[-1])
