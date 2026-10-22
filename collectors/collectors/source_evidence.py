"""Persist provider evidence and collection state atomically; no startup DDL."""
import json
from datetime import datetime, timezone
from collectors.db import get_pg_pool

async def save_cycle(source, records, detail, *, state='partial', error_code=None):
    pool = await get_pg_pool()
    now = datetime.now(timezone.utc)
    latest = max((r['observed_at'] for r in records), default=None)
    async with pool.acquire() as conn, conn.transaction():
        if records:
            await conn.executemany('''
                INSERT INTO source_evidence(source,evidence_id,observed_at,asn,family,resource,payload)
                VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)
                ON CONFLICT(source,evidence_id) DO UPDATE SET
                  observed_at=excluded.observed_at,asn=excluded.asn,family=excluded.family,
                  resource=excluded.resource,payload=excluded.payload
                WHERE excluded.observed_at > source_evidence.observed_at OR
                  (excluded.observed_at=source_evidence.observed_at AND excluded.payload IS DISTINCT FROM source_evidence.payload)
            ''', [(source,r['id'],r['observed_at'],r.get('asn'),r.get('family'),r.get('resource'),
                   json.dumps(r['payload'],allow_nan=False)) for r in records])
        success = now if error_code is None and state != 'unconfigured' else None
        await conn.execute('''
            INSERT INTO source_status(source,last_attempt,last_success,last_observation,state,records,detail,error_code)
            VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
            ON CONFLICT(source) DO UPDATE SET last_attempt=excluded.last_attempt,
              last_success=COALESCE(excluded.last_success,source_status.last_success),
              last_observation=GREATEST(excluded.last_observation,source_status.last_observation),
              state=excluded.state,records=excluded.records,detail=excluded.detail,error_code=excluded.error_code
        ''', source,now,success,latest,state,len(records),json.dumps(detail),error_code)

async def source_failure(source, exc):
    # Public status never includes connection strings or credential-bearing URLs.
    await save_cycle(source, [], {'message':'Collection failed; previous evidence may be stale.'},
                     state='error', error_code=type(exc).__name__)
