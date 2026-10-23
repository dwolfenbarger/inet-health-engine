"""Bounded observed-cohort summaries; no claim of complete or global coverage."""
import argparse,asyncio,importlib.util,json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
spec=importlib.util.spec_from_file_location('profile_retention',Path(__file__).with_name('profile-retention.py'))
profile=importlib.util.module_from_spec(spec);spec.loader.exec_module(profile)
SQL="""
WITH versions AS (
 SELECT *,count(*) OVER(PARTITION BY source,kind,evidence_id,observed_at) AS version_count,
 row_number() OVER(PARTITION BY source,kind,evidence_id,observed_at ORDER BY id DESC) AS rank
 FROM inet_retention.evidence WHERE observed_at >= $1 AND observed_at < $2
 AND ((source='atlas-mesh' AND kind='sample') OR (source IN ('atlas','ioda') AND kind='snapshot'))
), metrics AS (
 SELECT *,CASE WHEN source='atlas-mesh' THEN evidence_id
 WHEN source='atlas' THEN coalesce(payload->>'measurement_id','unknown')||':'||coalesce(payload->>'kind','unknown')
 ELSE coalesce(payload->>'entity_type','unknown') END AS cohort,
 CASE WHEN source='atlas-mesh' THEN (payload->>'rtt_med')::double precision
 WHEN source='atlas' THEN (payload->>'rtt_ms')::double precision END AS rtt,
 CASE WHEN source='atlas-mesh' THEN (payload->>'sent')::numeric
 WHEN source='atlas' AND payload->>'kind'='ping' AND jsonb_typeof(payload#>'{raw,sent}')='number' THEN (payload#>>'{raw,sent}')::numeric END AS sent,
 CASE WHEN source='atlas-mesh' THEN (payload->>'received')::numeric
 WHEN source='atlas' AND payload->>'kind'='ping' AND jsonb_typeof(payload#>'{raw,rcvd}')='number' THEN (payload#>>'{raw,rcvd}')::numeric END AS received
 FROM versions WHERE rank=1
), grouped AS (
 SELECT g.grain,date_trunc(g.grain,observed_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS bucket,
 source,coalesce(family,0)::smallint AS family,cohort,count(*) AS samples,
 sum(version_count) AS versions,count(*) FILTER(WHERE version_count>1) AS revised_samples,
 count(DISTINCT evidence_id) AS identities,min(observed_at) AS first_sample,max(observed_at) AS last_sample,
 sum(sent) FILTER(WHERE sent>0 AND sent=trunc(sent) AND received=trunc(received) AND received>=0 AND received<=sent) AS sent,
 sum(received) FILTER(WHERE sent>0 AND sent=trunc(sent) AND received=trunc(received) AND received>=0 AND received<=sent) AS received,
 count(*) FILTER(WHERE sent>0 AND sent=trunc(sent) AND received=trunc(received) AND received>=0 AND received<=sent) AS packet_counter_samples,
 count(*) FILTER(WHERE rtt>=0) AS rtt_samples,
 avg(rtt) FILTER(WHERE rtt>=0 AND source='atlas') AS mean_sample_rtt_ms,
 percentile_cont(0.5) WITHIN GROUP(ORDER BY rtt) FILTER(WHERE rtt>=0 AND source='atlas-mesh') AS median_sample_medians_ms
 FROM metrics CROSS JOIN (VALUES('hour'),('day')) g(grain)
 GROUP BY g.grain,bucket,source,coalesce(family,0),cohort
)
SELECT * FROM grouped WHERE bucket >= $1 AND bucket+CASE WHEN grain='hour' THEN interval '1 hour' ELSE interval '1 day' END <= $2
ORDER BY grain,bucket,source,family,cohort
"""
async def summarize(conn,start,end):
    if start.tzinfo is None or end.tzinfo is None or not timedelta(0)<end-start<=timedelta(hours=48):
        raise ValueError('Use aware timestamps and a positive window no larger than48 hours')
    async with conn.transaction(isolation='repeatable_read'):
        await conn.execute("SET LOCAL statement_timeout='30s'")
        await conn.execute("SET LOCAL lock_timeout='3s'")
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext('inet-retained-summaries'))"):
            raise RuntimeError('Summary writer already running')
        high=await conn.fetchval('SELECT coalesce(max(id),0) FROM inet_retention.evidence')
        policy=dict(await conn.fetchrow('SELECT * FROM inet_retention.policy WHERE id'))
        rows=await conn.fetch(SQL,start,end)
        for row in rows:
            item=dict(row);grain=item.pop('grain');bucket=item.pop('bucket')
            source=item.pop('source');family=item.pop('family');cohort=item.pop('cohort')
            for key in ['sent','received','versions']:item[key]=int(item[key]) if item[key] is not None else None
            for key in ['first_sample','last_sample']:item[key]=item[key].isoformat()
            item.update({'loss_percent':100*(item['sent']-item['received'])/item['sent'] if item['sent'] else None,
              'coverage':'Observed samples in configured source/cohort only; missing observations remain unknown.',
              'complete':False,'expected_samples':None,'capture_started_at':str(policy['started_at']),
              'captured_through_id':high,'capture_gaps_present':await conn.fetchval('SELECT EXISTS(SELECT 1 FROM inet_retention.gaps)'),
              'metric_basis':'Mesh: median of sample medians; Atlas: mean of returned sample RTT fields; loss: valid packet counters; IODA: event-start candidates, no health probability.'})
            await conn.execute('''INSERT INTO inet_retention.summaries(grain,bucket,source,family,cohort,algorithm,document)
              VALUES($1,$2,$3,$4,$5,'observed-cohort-v1',$6::jsonb)
              ON CONFLICT(grain,bucket,source,family,cohort,algorithm) DO UPDATE
              SET computed_at=clock_timestamp(),document=excluded.document''',grain,bucket,source,family,cohort,json.dumps(item,allow_nan=False))
        return {'summaries':len(rows),'captured_through_id':high,'from':start.isoformat(),'until':end.isoformat(),'complete':False}
async def main():
    p=argparse.ArgumentParser();p.add_argument('--until');p.add_argument('--hours',type=int,default=48);p.add_argument('--study',action='store_true');args=p.parse_args()
    profile.setup() # development host/local DB check before credentials are used
    import asyncpg
    conn=await asyncpg.connect(profile.os.environ['TIMESCALE_DSN'],command_timeout=35)
    try:
        while True:
            until=await conn.fetchval('SELECT enabled_until FROM inet_retention.policy WHERE id')
            if args.study and (until is None or datetime.now(timezone.utc)>=until):break
            end=datetime.fromisoformat(args.until) if args.until else datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
            print('RETAINED_SUMMARIES',json.dumps(await summarize(conn,end-timedelta(hours=args.hours),end)),flush=True)
            if not args.study:break
            await asyncio.sleep(3600)
    finally:await conn.close()
if __name__=='__main__':asyncio.run(main())
