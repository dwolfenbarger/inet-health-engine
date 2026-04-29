"""
api/routes_traffic.py
Traffic metrics + regional health endpoints.
"""

from fastapi import APIRouter, Query
from typing import Optional
from api.deps import get_pg_pool

router = APIRouter(prefix="/traffic", tags=["traffic"])


@router.get("/regions")
async def traffic_regions(hours: int = Query(1, ge=1, le=24)):
    """Regional internet health summary from traffic_metrics table."""
    pool = await get_pg_pool()
    rows = await pool.fetch("""
        SELECT region,
               round(avg(value)::numeric, 3)  AS avg_value,
               count(*)                        AS sample_count,
               max(time)                       AS last_seen
        FROM traffic_metrics
        WHERE time > NOW() - ($1 || ' hours')::INTERVAL
          AND region IS NOT NULL
        GROUP BY region
        ORDER BY region
    """, str(hours))
    return {"regions": [dict(r) for r in rows]}


@router.get("/metrics")
async def traffic_metrics(
    metric_type: Optional[str] = Query(None),
    region:      Optional[str] = Query(None),
    asn:         Optional[int] = Query(None),
    hours:       int           = Query(1, ge=1, le=24),
    limit:       int           = Query(200, ge=1, le=1000),
):
    """Raw traffic metric time series."""
    pool = await get_pg_pool()

    conditions = ["time > NOW() - ($1 || ' hours')::INTERVAL"]
    args       = [str(hours)]
    idx        = 2

    if metric_type:
        conditions.append(f"metric_type = ${idx}"); args.append(metric_type); idx += 1
    if region:
        conditions.append(f"region = ${idx}"); args.append(region); idx += 1
    if asn:
        conditions.append(f"asn = ${idx}"); args.append(asn); idx += 1

    rows = await pool.fetch(
        f"""
        SELECT time, region, country_code, asn, metric_type, value, source
        FROM traffic_metrics
        WHERE {' AND '.join(conditions)}
        ORDER BY time DESC LIMIT ${idx}
        """,
        *args, limit,
    )
    return {"count": len(rows), "metrics": [dict(r) for r in rows]}


@router.get("/outages")
async def outage_events(
    hours:       int = Query(6,  ge=1, le=72),
    severity_min: int = Query(3, ge=1, le=5),
):
    """Active outage events from network_events table."""
    pool = await get_pg_pool()
    rows = await pool.fetch("""
        SELECT time, event_id, event_type, severity, confidence,
               affected_regions, affected_asns, summary, source_count, resolved_at
        FROM network_events
        WHERE time > NOW() - ($1 || ' hours')::INTERVAL
          AND event_type = 'outage'
          AND severity >= $2
        ORDER BY severity DESC, time DESC
        LIMIT 50
    """, str(hours), severity_min)
    return {"count": len(rows), "outages": [dict(r) for r in rows]}
