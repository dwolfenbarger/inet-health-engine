"""Read existing public Atlas anchor-mesh evidence; never schedules probes."""
import argparse,asyncio,json,math,signal,statistics
from pathlib import Path
from datetime import datetime,timedelta,timezone
from ipaddress import ip_address
import asyncpg,httpx
from collectors.db import get_pg_pool,close_pg_pool
from collectors.config import settings
from collectors.source_evidence import save_cycle
UTC=timezone.utc
SOURCE='atlas-mesh'
BASE='https://atlas.ripe.net/api/v2/'

def number(value):
    if isinstance(value,bool):raise ValueError('Boolean numeric value')
    n=float(value)
    if not math.isfinite(n) or n<0:raise ValueError('Invalid numeric value')
    return n

def samples(data,pair,now):
    if int(data['msm_id'])!=pair['measurement_id'] or int(data['probe_id'])!=pair['source_probe'] or data['type']!='ping' or data['resolution']!='none':
        raise ValueError('Atlas result identity differs')
    rows=[];seen=set()
    if len(data['data'])>500:raise ValueError('Unexpected unbounded mesh series')
    for raw in data['data']:
        at=datetime.fromtimestamp(number(raw['timestamp']),UTC)
        if not now-timedelta(hours=26)<=at<=now+timedelta(minutes=2):raise ValueError('Result outside requested clock window')
        if at in seen:raise ValueError('Duplicate timestamp')
        seen.add(at)
        sent=number(raw['sent']);received=number(raw['received'])
        if sent!=int(sent) or received!=int(received) or received>sent:raise ValueError('Invalid packet counters')
        rtt=None if received==0 or raw.get('rtt_med') is None else number(raw['rtt_med'])
        rows.append({'at':at,'sent':int(sent),'received':int(received),'rtt':rtt})
    return sorted(rows,key=lambda r:r['at'])

def summarize(rows,now):
    cutoff=now-timedelta(minutes=15)
    current=[r for r in rows if r['at']>=cutoff]
    baseline=[r for r in rows if now-timedelta(hours=25)<=r['at']<cutoff]
    def stats(group):
        sent=sum(r['sent'] for r in group);received=sum(r['received'] for r in group)
        values=[r['rtt'] for r in group if r['rtt'] is not None]
        return {'samples':len(group),'sent':sent,'received':received,'loss_percent':100*(sent-received)/sent if sent else None,
                'median_of_sample_medians_ms':statistics.median(values) if values else None,
                'first':group[0]['at'].isoformat() if group else None,'last':group[-1]['at'].isoformat() if group else None}
    recent,prior=stats(current),stats(baseline)
    established=len(baseline)>=60 and bool(baseline) and (baseline[-1]['at']-baseline[0]['at']).total_seconds()>=21600
    delta=None
    if established and recent['median_of_sample_medians_ms'] is not None and prior['median_of_sample_medians_ms'] is not None:
        delta=recent['median_of_sample_medians_ms']-prior['median_of_sample_medians_ms']
    state='insufficient_recent_samples';candidates=[]
    if len(current)>=3 and recent['sent']>=9:
        state='observations_available' if established else 'baseline_building'
        if established and recent['loss_percent'] is not None and prior['loss_percent'] is not None and recent['loss_percent']-prior['loss_percent']>=5:
            candidates.append('increased_packet_loss')
        if delta is not None and delta>max(10,prior['median_of_sample_medians_ms']*.25):candidates.append('increased_latency')
        if candidates:state='measurement_change_candidate'
    return {'current':recent,'baseline':prior,'baseline_established':established,'delta_ms':delta,'state':state,
            'candidates':candidates,'health_score':None,
            'limitations':['Endpoint-to-endpoint samples do not locate congestion or establish an ASN-wide outage.',
              'RTT is the median of per-measurement packet medians; loss uses packet counts.',
              'Missing results are unknown; returned packets lost are retained.',
              'Baseline is the preceding 25-hour window excluding the latest 15 minutes; candidates are not confirmed incidents.']}

def check_measurement(meta,pair,target):
    if meta['id']!=pair['measurement_id'] or meta['af']!=pair['family'] or meta['type']!='ping' or not meta.get('is_public'):
        raise ValueError('Measurement metadata differs')
    if not {'anchoring','mesh'}<=set(meta.get('tags',[])) or meta.get('status',{}).get('id')!=2:raise ValueError('Not an ongoing public anchoring mesh')
    if ip_address(meta['target_ip'])!=ip_address(target[f"ip_v{pair['family']}"]):raise ValueError('Target address changed')
    target_asn(meta) # Validate optional metadata without inventing a destination ASN.

def target_asn(meta):
    value=meta.get('target_asn')
    if value is None:return None
    if isinstance(value,bool) or not 1<=int(value)<=4294967295:raise ValueError('Invalid target ASN metadata')
    return int(value)

async def get_json(client,url,params=None,max_bytes=2*1024**2):
    async with client.stream('GET',url,params=params) as response:
        response.raise_for_status();raw=bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw)>max_bytes:raise ValueError('Provider response exceeds bounded input')
    return json.loads(raw,parse_constant=lambda _:None)

async def cycle(config):
    anchors={a['probe']:a for a in config['anchors']};pairs=config['pairs']
    if not 1<=len(pairs)<=32 or len({p['id'] for p in pairs})!=len(pairs):raise ValueError('Invalid mesh configuration size or duplicate pair')
    now=datetime.now(UTC);pool=await get_pg_pool();records=[];errors=[]
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
        metadata={};probes={}
        for pair in pairs:
            source,target=anchors[pair['source_probe']],anchors[pair['target_probe']]
            try:
                mid=pair['measurement_id'];af=pair['family']
                if mid not in metadata:metadata[mid]=await get_json(client,BASE+f'measurements/{mid}/')
                check_measurement(metadata[mid],pair,target)
                for anchor in [source,target]:
                    ident=anchor['probe']
                    if ident not in probes:probes[ident]=await get_json(client,BASE+f'probes/{ident}/')
                    probe=probes[ident]
                    if probe['id']!=ident or int(probe.get(f'asn_v{af}') or 0)!=anchor[f'as_v{af}']:
                        raise ValueError('Anchor ASN identity changed')
                params={'probe_ids':source['probe'],'start':int((now-timedelta(hours=25)).timestamp()),'stop':int(now.timestamp()),'resolution':'none'}
                url=BASE+f'measurements/{mid}/ping-stats/'
                data=await get_json(client,url,params);rows=samples(data,pair,now)
                if not rows:raise ValueError('No measurement results')
                summary=summarize(rows,now)
                observed=rows[-1]['at'] if rows else now
                payload={'pair':pair,'source':source,'target':target,'source_asn':source[f'as_v{af}'],'target_asn':target_asn(metadata[mid]),
                    'asn_basis':'Source ASN is current probe metadata; target ASN is separate measurement metadata and may be unknown. Anchor as_v4/as_v6 describe its probe network.',**summary,'observed_at':rows[-1]['at'].isoformat() if rows else None,
                    'fetched_at':now.isoformat(),'measurement_url':f'https://atlas.ripe.net/measurements/{mid}/',
                    'raw_evidence_url':str(httpx.URL(url,params=params)),
                    'trace_evidence_url':BASE+f"measurements/{pair['trace_measurement_id']}/results/?probe_ids={source['probe']}&start={int((now-timedelta(hours=1)).timestamp())}" if pair.get('trace_measurement_id') else None,
                    'source_probe_status':probes[source['probe']].get('status'), 'target_probe_status':probes[target['probe']].get('status'),
                    'raw_stats':data}
                records.append({'id':pair['id'],'observed_at':observed,'family':af,'asn':None,
                    'resource':f"probe:{source['probe']}->probe:{target['probe']}",'payload':payload})
            except Exception as exc:
                errors.append({'pair':pair['id'],'error_type':type(exc).__name__})
            await asyncio.sleep(.1)
    detail={'scope':'Configured public Atlas anchor-to-anchor directions; endpoint ASN metadata only',
            'configured_directions':len(pairs),'collected_directions':len(records),'errors':errors,
            'anchors':list(anchors.values()),'pairs':pairs,'collection_interval_seconds':300,
            'limitations':['Atlas mesh shares Atlas lineage with K-root measurements.',
                'No new measurements are scheduled. No global health percentage or ASN-wide attribution.']}
    await save_cycle(SOURCE,records,detail,state='partial' if records else 'error',error_code='PartialDirectionFailure' if errors else None)
    print('ATLAS_MESH_CYCLE',len(records),'of',len(pairs),'directions; errors',json.dumps(errors),flush=True)

async def main(path,once=False):
    config=json.loads(Path(path).read_text());lock=await asyncpg.connect(settings.timescale_dsn)
    if not await lock.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-atlas-mesh'))"):raise RuntimeError('Another mesh collector owns the database')
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in [signal.SIGTERM,signal.SIGINT]:loop.add_signal_handler(sig,stop.set)
    try:
        while not stop.is_set():
            try:await asyncio.wait_for(cycle(config),timeout=240)
            except Exception as exc:
                await save_cycle(SOURCE,[],{'scope':'Configured Atlas mesh; collection failed',
                    'anchors':config['anchors'],'pairs':config['pairs'],
                    'errors':[{'pair':p['id'],'error_type':type(exc).__name__} for p in config['pairs']]},
                    state='error',error_code=type(exc).__name__)
                print('ATLAS_MESH_PENDING',type(exc).__name__,flush=True)
            if once:break
            try:await asyncio.wait_for(stop.wait(),timeout=300)
            except asyncio.TimeoutError:pass
    finally:await lock.close();await close_pg_pool()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    asyncio.run(main(args.config,args.once))
