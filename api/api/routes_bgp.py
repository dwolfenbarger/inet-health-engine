"""
api/routes/bgp.py
BGP data endpoints — updates, anomalies, path lookup.
All queries hit TimescaleDB directly via asyncpg.
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import asyncio
from api.deps import get_pg_pool

# Compact AS name lookup for anomaly enrichment
# ASN lookup — single source of truth from routes_globe
from api.routes_globe import ASN_GEO as _BGP_ASN_META  # type: ignore

router = APIRouter(prefix="/bgp", tags=["bgp"])


@router.get("/updates")
async def bgp_updates(
    limit:  int           = Query(100, ge=1, le=1000),
    prefix: Optional[str] = Query(None),
    asn:    Optional[int] = Query(None),
    change_type: Optional[str] = Query(None, pattern="^(announce|withdraw|update)$"),
):
    """
    Recent BGP updates from TimescaleDB.
    Filterable by prefix, origin ASN, and change type.
    """
    pool = await get_pg_pool()

    conditions = []
    args       = []
    idx        = 1

    if prefix:
        conditions.append(f"prefix = ${idx}"); args.append(prefix); idx += 1
    if asn:
        conditions.append(f"origin_asn = ${idx}"); args.append(asn); idx += 1
    if change_type:
        conditions.append(f"change_type = ${idx}"); args.append(change_type); idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    rows = await pool.fetch(
        f"""
        SELECT time, prefix, origin_asn, as_path, communities,
               change_type, collector, peer_asn, next_hop, rpki_status
        FROM bgp_updates
        {where}
        ORDER BY time DESC
        LIMIT ${idx}
        """,
        *args, limit,
    )

    return {
        "count":   len(rows),
        "updates": [dict(r) for r in rows],
    }


@router.get("/anomalies")
async def bgp_anomalies(
    limit:       int           = Query(50,  ge=1, le=500),
    severity_min: int          = Query(1,   ge=1, le=5),
    event_type:  Optional[str] = Query(None),
    hours:       int           = Query(24,  ge=1, le=168),
):
    """
    Detected BGP anomalies — hijacks, leaks, flaps, withdrawal surges.
    Sorted by severity descending, then time descending.
    """
    pool = await get_pg_pool()

    conditions = [
        "time > NOW() - ($1 || ' hours')::INTERVAL",
        "severity >= $2",
    ]
    args = [str(hours), severity_min]
    idx  = 3

    if event_type:
        conditions.append(f"event_type = ${idx}")
        args.append(event_type)
        idx += 1

    rows = await pool.fetch(
        f"""
        SELECT time, event_id, event_type, affected_prefix,
               origin_asn, expected_asn, severity, confidence, source
        FROM bgp_anomalies
        WHERE {' AND '.join(conditions)}
        ORDER BY severity DESC, confidence DESC, time DESC
        LIMIT ${idx}
        """,
        *args, limit,
    )

    # Bulk-fetch RPKI statuses from Redis in a single pipeline
    rpki_map: dict[str, str] = {}
    try:
        redis = await get_redis()
        pipe = redis.pipeline()
        for r in rows:
            pfx = r["affected_prefix"]; asn = r["origin_asn"]
            if pfx and asn:
                pipe.get(f"rpki:{pfx}:{asn}")
            else:
                pipe.get("rpki:__none__")
        rpki_vals = await pipe.execute()
        for r, val in zip(rows, rpki_vals):
            pfx = r["affected_prefix"]; asn = r["origin_asn"]
            if pfx and asn:
                rpki_map[f"{pfx}:{asn}"] = val or "unknown"
    except Exception:
        pass

    def _enrich(r):
        d = dict(r)
        g = _BGP_ASN_META.get(d.get("origin_asn") or 0)
        d["origin_name"]    = g[3] if g else None
        d["origin_country"] = g[2] if g else None
        eg = _BGP_ASN_META.get(d.get("expected_asn") or 0)
        d["expected_name"]  = eg[3] if eg else None
        if isinstance(d.get("event_id"), bytes):
            d["event_id"] = str(d["event_id"])
        pfx = d.get("affected_prefix"); asn = d.get("origin_asn")
        d["rpki_status"] = rpki_map.get(f"{pfx}:{asn}", "unknown")
        return d
    return {
        "count":     len(rows),
        "anomalies": [_enrich(r) for r in rows],
    }


@router.get("/summary")
async def bgp_summary():
    """
    High-level BGP summary for the NOC status bar.
    Returns counts, top active ASes, and top flapping prefixes.
    """
    pool = await get_pg_pool()

    updates_1h, anomalies_1h, top_asns, top_prefixes = await asyncio.gather(
        pool.fetchval("SELECT count(*) FROM bgp_updates WHERE time > NOW() - INTERVAL '1 hour'"),
        pool.fetchval("SELECT count(*) FROM bgp_anomalies WHERE time > NOW() - INTERVAL '1 hour'"),
        pool.fetch("""
            SELECT origin_asn, count(*) as update_count
            FROM bgp_updates WHERE time > NOW() - INTERVAL '1 hour'
            AND origin_asn IS NOT NULL
            GROUP BY origin_asn ORDER BY update_count DESC LIMIT 5
        """),
        pool.fetch("""
            SELECT prefix, count(*) as change_count
            FROM bgp_updates WHERE time > NOW() - INTERVAL '1 hour'
            GROUP BY prefix ORDER BY change_count DESC LIMIT 5
        """),
    )

    return {
        "updates_last_1h":    updates_1h,
        "anomalies_last_1h":  anomalies_1h,
        "top_active_asns":    [dict(r) for r in top_asns],
        "top_active_prefixes": [dict(r) for r in top_prefixes],
    }
