"""Locally validated origin evidence for recently observed prefix/origin pairs.

Routinator verifies the five RIR trust anchors. This adapter reports the actual
VRP snapshot time and preserves the matching/unmatched VRPs and invalid reason.
It never converts HTTP errors or malformed responses into 'not-found'.
"""
import asyncio
import redis.asyncio as aioredis
from datetime import datetime, timezone, timedelta
import httpx
from collectors.config import settings
from collectors.vrp_index import VRPIndex
from collectors.db import get_pg_pool
from collectors.source_evidence import save_cycle, source_failure

SOURCE = 'rpki-local'
BATCH = 500
MAX_PAIRS = 50000

def parse_validations(data, requested, now=None):
    now = now or datetime.now(timezone.utc)
    generated = datetime.fromisoformat(data['generatedTime'].replace('Z','+00:00'))
    if generated.tzinfo is None or generated > now+timedelta(minutes=2) or now-generated > timedelta(minutes=20):
        raise ValueError('Validator snapshot is stale or has an invalid clock')
    expected = set(requested)
    found = {}
    for entry in data['validated_routes']:
        route, validity = entry['route'], entry['validity']
        pair = (route['prefix'],int(str(route['origin_asn']).removeprefix('AS')))
        if pair not in expected or pair in found:
            raise ValueError('Unexpected or duplicate validation result')
        state = validity['state']
        if state not in ['valid','invalid','not-found']:
            raise ValueError('Unknown validation state')
        found[pair] = {'id':f'{pair[0]}|{pair[1]}','observed_at':generated,
            'asn':pair[1], 'family':6 if ':' in pair[0] else 4,'resource':pair[0],
            'payload':{'prefix':pair[0],'origin_asn':pair[1],'status':state,
                       'validity':validity,'generated_at':generated.isoformat(),
                       'checked_at':now.isoformat(),'source':SOURCE}}
    if set(found) != expected:
        raise ValueError('Missing validation results')
    return list(found.values())

async def run_collection_cycle():
    pool = await get_pg_pool()
    async with pool.acquire() as lock:
        if not await lock.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-rpki-local'))"):
            return []
        try:
            # Prioritize pairs with no validation or oldest snapshot; revisit as
            # origin observations arrive. This is explicitly bounded coverage.
            pairs = await pool.fetch('''
              WITH pairs AS (
                SELECT DISTINCT prefix,origin_asn FROM bgp_updates
                WHERE time > now()-interval '1 hour' AND origin_asn IS NOT NULL
                  AND origin_asn > 0 AND collector NOT LIKE 'stub%'
              ) SELECT p.prefix,p.origin_asn FROM pairs p
                LEFT JOIN source_evidence e ON e.source='rpki-local'
                   AND e.evidence_id=p.prefix||'|'||p.origin_asn::text
                ORDER BY e.observed_at NULLS FIRST,p.prefix,p.origin_asn LIMIT $1
            ''',MAX_PAIRS+1)
            capped = len(pairs)>MAX_PAIRS
            pairs = [(p['prefix'],p['origin_asn']) for p in pairs[:MAX_PAIRS]]
            records=[];validator={}
            async with httpx.AsyncClient(base_url=settings.routinator_url,timeout=30) as client:
                status=await client.get('/api/v1/status');status.raise_for_status();status=status.json()
                tals=status['tals']
                if set(tals) != {'afrinic','apnic','arin','lacnic','ripe'}:
                    raise ValueError('Production RIR trust anchors missing')
                validator={'version':status['version'],'last_update_done':status['lastUpdateDone'],
                    'tals':{k:{f:v.get(f) for f in ['vrpsFinal','validPublicationPoints',
                              'rejectedPublicationPoints','missingManifests','staleManifests']} for k,v in tals.items()}}
                response=await client.get('/json',timeout=120);response.raise_for_status()
                snapshot=response.json()
                generated=snapshot['metadata']['generatedTime']
                index=await asyncio.to_thread(VRPIndex,snapshot['roas'])
                validator['vrp_count']=index.count
                del snapshot
                # Origin checks use covering prefixes for both families. Preserve
                # all matching and conflicting VRPs, including AS0 authorizations.
                answers={'generatedTime':generated,'validated_routes':[
                    {'route':{'prefix':p,'origin_asn':f'AS{a}'},'validity':index.validate(p,a)}
                    for p,a in pairs]}
                records=parse_validations(answers,pairs)
                del index,answers
            await save_cycle(SOURCE,records,{'scope':'Recently observed prefix/origin pairs from the last hour',
                'cycle_limit':MAX_PAIRS,'cycle_capped':capped,'validated_pairs':len(records),
                'validator':validator,
                'limitations':['RPKI validates origin authorization, not AS path safety or hijacking.',
                  'Results depend on the locally available validated repositories; unavailable ROAs can affect coverage.',
                  'Recently observed pairs are a subset of the global routing table.']})
            # Versioned keys prevent reuse of answers from the former public
            # endpoint. Cache expiry is tied to the validated snapshot, not fetch time.
            async with aioredis.from_url(settings.redis_url,decode_responses=True) as redis:
                pipe=redis.pipeline()
                now=datetime.now(timezone.utc)
                for r in records:
                    p=r['payload'];ttl=int(1200-(now-r['observed_at']).total_seconds())
                    if ttl>0:pipe.set(f"rpki:v2:{p['prefix']}:{p['origin_asn']}",p['status'],ex=ttl)
                await pipe.execute()
            return records
        except Exception as exc:
            await source_failure(SOURCE,exc)
            raise
        finally:
            await lock.execute("SELECT pg_advisory_unlock(hashtext('inet-rpki-local'))")
