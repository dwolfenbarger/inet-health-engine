"""Public Atlas measurements and IODA events with explicit source lineage.

Provider payloads remain available as evidence. No source score is converted to
our confidence, and neither an empty event list nor a successful HTTP call is a
network all-clear. These adapters do not initiate new active measurements.
"""
from datetime import datetime,timezone,timedelta
import hashlib,json,math,time
import httpx
from collectors.db import get_pg_pool
from collectors.source_evidence import save_cycle,source_failure

ATLAS_BASE='https://atlas.ripe.net/api/v2'
IODA_BASE='https://api.ioda.inetintel.cc.gatech.edu/v2'
# Public ongoing built-ins, metadata checked on every cycle, IPv4 and IPv6.
MEASUREMENTS=[1001,2001,10001]

def atlas_record(raw, metadata, now):
    observed=datetime.fromtimestamp(raw['timestamp'],timezone.utc)
    if not now-timedelta(hours=1) <= observed <= now+timedelta(minutes=2):
        return None
    kind,af=metadata['type'],metadata['af']
    if raw.get('type',kind) != kind or raw.get('af',af) != af or not raw.get('prb_id'):
        raise ValueError('Atlas metadata/result mismatch')
    payload={'measurement_id':raw['msm_id'],'probe_id':raw['prb_id'],
        'kind':kind,'family':af,'source_address':raw.get('from'),
        'target':raw.get('dst_addr'),'raw':raw,'rtt_ms':None,'loss_percent':None}
    if kind=='ping':
        sent,rcvd=raw.get('sent'),raw.get('rcvd')
        if isinstance(sent,int) and sent>0 and isinstance(rcvd,int) and 0<=rcvd<=sent:
            payload['loss_percent']=100*(sent-rcvd)/sent
        avg=raw.get('avg')
        if isinstance(avg,(int,float)) and math.isfinite(avg) and avg>=0:
            payload['rtt_ms']=avg
    elif kind=='dns':
        result=raw.get('result',{})
        if isinstance(result,dict):
            rt=result.get('rt')
            if isinstance(rt,(int,float)) and math.isfinite(rt) and rt>=0:payload['rtt_ms']=rt
        # DNS timeouts/errors remain in raw evidence; no fabricated zero RTT/loss.
    else:
        raise ValueError('Unsupported Atlas measurement type')
    return {'id':f"{raw['msm_id']}|{raw['prb_id']}",'observed_at':observed,
        'family':af,'resource':raw.get('dst_addr'),'payload':payload}

async def run_atlas_cycle():
    pool=await get_pg_pool()
    async with pool.acquire() as lock:
        if not await lock.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-atlas-public'))"):return []
        records=[];inventory=[];failures=[]
        try:
            async with httpx.AsyncClient(timeout=45,follow_redirects=True) as client:
                for msm in MEASUREMENTS:
                    try:
                        meta=await client.get(f'{ATLAS_BASE}/measurements/{msm}/');meta.raise_for_status();meta=meta.json()
                        if meta.get('status',{}).get('name')!='Ongoing':raise ValueError('Atlas measurement stopped')
                        resp=await client.get(f'{ATLAS_BASE}/measurements/{msm}/latest/');resp.raise_for_status()
                        raw=resp.json()
                        if not isinstance(raw,list):raise ValueError('Unexpected Atlas results')
                        now=datetime.now(timezone.utc)
                        parsed=[];parse_errors=0
                        for item in raw:
                            try:
                                record=atlas_record(item,meta,now)
                                if record is not None:parsed.append(record)
                            except (KeyError,TypeError,ValueError,OverflowError):
                                parse_errors+=1
                        if parse_errors:failures.append({'id':msm,'error':'MalformedProbeResults','count':parse_errors})
                        records.extend(parsed)
                        inventory.append({'id':msm,'kind':meta['type'],'family':meta['af'],
                            'target':meta.get('target_ip'),'interval_seconds':meta.get('interval'),
                            'fresh_probes':len({r['payload']['probe_id'] for r in parsed}),
                            'stale_results_excluded':len(raw)-len(parsed)-parse_errors,
                            'malformed_results_excluded':parse_errors})
                    except Exception as exc:failures.append({'id':msm,'error':type(exc).__name__})
            if not inventory:raise RuntimeError('No Atlas measurement could be read')
            await save_cycle('atlas',records,{'scope':'Public built-in measurements to K-root; latest result per probe, last hour',
                'measurements':inventory,'failures':failures,
                'probe_count':len({r['payload']['probe_id'] for r in records}),
                'limitations':['Only these targets and participating probes are observed.',
                  'Probe source address is not an ASN. ASN and country attribution are not yet available.',
                  'ICMP loss or DNS delay alone cannot prove congestion.']},
                error_code='PartialMeasurementFailure' if failures else None)
            return records
        except Exception as exc:
            await source_failure('atlas',exc);raise
        finally:await lock.execute("SELECT pg_advisory_unlock(hashtext('inet-atlas-public'))")

def ioda_record(raw,entity_type):
    # IODA's native event format retains provider score and datasource evidence.
    entity=raw['entity']
    code=str(entity['code'])
    if entity.get('type')!=entity_type:raise ValueError('Unexpected IODA entity')
    start=raw['from']
    observed=datetime.fromtimestamp(float(start),timezone.utc)
    identity={k:raw.get(k) for k in ['entity','from','datasource','method']}
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    return {'id':key,'observed_at':observed,
        'asn':int(code.removeprefix('AS')) if entity_type=='asn' else None,
        'resource':f'{entity_type}/{code}',
        'payload':{'kind':'outage-candidate','entity_type':entity_type,'entity_code':code,
          'raw':raw,'lineage':'IODA BGP reuses RIS and RouteViews; active probing and telescope are separate modalities.',
          'confidence':None}}

async def run_ioda_cycle():
    pool=await get_pg_pool()
    async with pool.acquire() as lock:
        if not await lock.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-ioda-public'))"):return []
        try:
            end=int(time.time());begin=end-3600
            records=[];capped=False;inventory=[]
            async with httpx.AsyncClient(timeout=45,follow_redirects=True) as client:
                for entity_type in ['country','asn']:
                    count=0
                    for page in range(1,11):
                        resp=await client.get(f'{IODA_BASE}/outages/events',params={
                            'entityType':entity_type,'from':begin,'until':end,'format':'ioda',
                            'includeAlerts':'true','limit':100,'page':page,'extendWindow':3600})
                        resp.raise_for_status();body=resp.json()
                        if body.get('error') is not None:raise ValueError('IODA provider error')
                        raw=body['data']
                        if not isinstance(raw,list):raise ValueError('Unexpected IODA response')
                        records.extend(ioda_record(d,entity_type) for d in raw);count+=len(raw)
                        if len(raw)<100:break
                        if page==10:capped=True
                    inventory.append({'entity_type':entity_type,'events':count})
            await save_cycle('ioda',records,{'scope':'Provider event candidates intersecting the last hour',
              'window_start':begin,'window_end':end,'entities':inventory,'page_capped':capped,
              'limitations':['A provider score is not a probability or independent verification.',
                'IODA routing observations overlap RIS/RouteViews; modalities must be inspected separately.',
                'No reported events is not proof of healthy reachability.']})
            return records
        except Exception as exc:
            await source_failure('ioda',exc);raise
        finally:await lock.execute("SELECT pg_advisory_unlock(hashtext('inet-ioda-public'))")
