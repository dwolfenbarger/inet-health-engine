"""
api/routes_events_lifecycle.py

Event lifecycle model: open → escalated → resolved
Cross-references RIS anomalies against Cloudflare Radar network_events
to produce multi-source confirmed events with compounded confidence.

Cross-reference logic:
  - For each RIS anomaly, scan network_events for matching prefix or ASN
    within ±30 minutes
  - Match: confidence compounds as 1 - (1-c1)*(1-c2)  (independent evidence)
  - Multi-source events get source_count incremented and multi_source=true

Event lifecycle table: bgp_event_lifecycle (TimescaleDB hypertable)
  open → escalated (if ongoing > 15 min or severity bumped)
  → resolved (when no matching anomaly in last 10 min)
"""
from fastapi import APIRouter, Query
from api.deps import get_pg_pool
import json, time as _time
from api.deps import get_redis

# ASN lookup — single source of truth from routes_globe
from api.routes_globe import ASN_GEO as _BGP_ASN_META  # type: ignore

def _asn_name(asn) -> str | None:
    g = _BGP_ASN_META.get(asn or 0); return g[3] if g else None
def _asn_country(asn) -> str | None:
    g = _BGP_ASN_META.get(asn or 0); return g[2] if g else None
from datetime import datetime, timezone

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/cross-reference")
async def cross_reference(window_m: int = Query(30, ge=5, le=120)):
    """
    Cross-reference RIS BGP anomalies against Cloudflare Radar events.
    Returns confirmed multi-source events with compounded confidence.
    Uses Python-side join on pre-aggregated data for Pi performance.
    """
    """
    Cross-reference RIS BGP anomalies against Cloudflare Radar events.
    Returns confirmed multi-source events with compounded confidence.
    """
    pool = await get_pg_pool()

    # Query 1: recent RIS anomalies
    ris_rows = await pool.fetch("""
        SELECT DISTINCT ON (affected_prefix, event_type)
            event_id, event_type, affected_prefix,
            origin_asn, expected_asn, confidence, severity
        FROM bgp_anomalies
        WHERE source LIKE 'ris/%%'
          AND time > NOW() - ($1 || ' minutes')::INTERVAL
          AND confidence > 0.50
        ORDER BY affected_prefix, event_type, confidence DESC
        LIMIT 200
    """, str(window_m))

    # Query 2: radar events — extract leak_asn + all ASNs in leak_seg
    radar_rows = await pool.fetch("""
        SELECT event_id, event_type, confidence, severity,
               (raw_signals->>'leak_asn')::integer AS leak_asn,
               ARRAY(
                 SELECT elem::integer
                 FROM jsonb_array_elements_text(
                   COALESCE(raw_signals->'leak_seg', '[]'::jsonb)
                 ) elem
                 WHERE elem ~ '^[0-9]+$'
               ) AS seg_asns
        FROM network_events
        WHERE time > NOW() - INTERVAL '24 hours'
          AND confidence > 0.0
          AND raw_signals IS NOT NULL
          AND raw_signals->>'leak_asn' IS NOT NULL
        LIMIT 5000
    """)

    # Python-side join — index by all ASNs in the leak path
    radar_by_asn: dict = {}
    for r in radar_rows:
        # Index by leak_asn (primary)
        if r["leak_asn"]:
            if r["leak_asn"] not in radar_by_asn:
                radar_by_asn[r["leak_asn"]] = r
        # Also index by every ASN in the leak segment path
        for seg_asn in (r["seg_asns"] or []):
            if seg_asn and seg_asn not in radar_by_asn:
                radar_by_asn[seg_asn] = r

    rows_built = []
    for r in ris_rows:
        radar_match = radar_by_asn.get(r["origin_asn"]) or radar_by_asn.get(r["expected_asn"])
        radar_conf  = float(radar_match["confidence"]) if radar_match else 0.0
        compound    = 1.0 - (1.0 - float(r["confidence"])) * (1.0 - radar_conf)
        rows_built.append({
            "ris_event_id":      str(r["event_id"]),
            "event_type":        r["event_type"],
            "affected_prefix":   r["affected_prefix"],
            "origin_asn":        r["origin_asn"],
            "expected_asn":      r["expected_asn"],
            "ris_conf":          float(r["confidence"]),
            "ris_sev":           r["severity"],
            "radar_event_id":    str(radar_match["event_id"]) if radar_match else None,
            "radar_type":        radar_match["event_type"] if radar_match else None,
            "radar_conf":        radar_conf if radar_match else None,
            "compound_conf":     compound,
            "multi_source":      radar_match is not None,
        })
    rows_built.sort(key=lambda x: (-x["compound_conf"], -x["ris_sev"]))

    events = []
    for r in rows_built[:100]:
        events.append({
            "ris_event_id":    r["ris_event_id"],
            "event_type":      r["event_type"],
            "affected_prefix": r["affected_prefix"],
            "origin_asn":      r["origin_asn"],
            "expected_asn":    r["expected_asn"],
            "ris_confidence":  round(r["ris_conf"], 3),
            "ris_severity":    r["ris_sev"],
            "radar_event_id":  r["radar_event_id"],
            "radar_type":      r["radar_type"],
            "radar_confidence":round(r["radar_conf"], 3) if r["radar_conf"] else None,
            "compound_confidence": round(r["compound_conf"], 3),
            "multi_source":    r["multi_source"],
            "origin_name":     _asn_name(r["origin_asn"]),
            "origin_country":  _asn_country(r["origin_asn"]),
        })

    multi   = [e for e in events if e["multi_source"]]
    single  = [e for e in events if not e["multi_source"]]

    return {
        "window_m":    window_m,
        "total":       len(events),
        "multi_source_confirmed": len(multi),
        "single_source":          len(single),
        "events":      events,
        "timestamp":   datetime.now(tz=timezone.utc).isoformat(),
    }


@router.get("/lifecycle")
async def event_lifecycle(
    status: str = Query("all", regex="^(open|escalated|resolved|all)$"),
    limit: int = Query(50, ge=1, le=200)
):
    """
    BGP event lifecycle — open/escalated/resolved model derived from
    bgp_anomalies history. Events that persist > 15 min are escalated.
    Events with no matching anomaly in last 10 min are marked resolved.
    """
    # Redis cache — lifecycle query is expensive (5-10s on Pi)
    # Cache for 30s; stale-while-revalidate pattern
    cache_key = f"lifecycle:{status}:{limit}"
    try:
        redis = await get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    pool = await get_pg_pool()

    rows = await pool.fetch("""
        WITH event_windows AS (
            SELECT
                event_type,
                affected_prefix,
                origin_asn,
                expected_asn,
                MIN(time)        AS first_seen,
                MAX(time)        AS last_seen,
                MAX(confidence)  AS peak_confidence,
                MAX(severity)    AS peak_severity,
                COUNT(*)         AS occurrence_count,
                EXTRACT(EPOCH FROM (MAX(time) - MIN(time))) AS duration_s
            FROM bgp_anomalies
            WHERE source LIKE 'ris/%%'
              AND confidence > 0.5
              AND time > NOW() - INTERVAL '24 hours'
            GROUP BY event_type, affected_prefix, origin_asn, expected_asn
        ),
        lifecycle AS (
            SELECT *,
                CASE
                    WHEN last_seen < NOW() - INTERVAL '15 minutes'
                        THEN 'resolved'
                    WHEN (duration_s > 900 AND occurrence_count >= 3)
                        OR occurrence_count >= 10
                        THEN 'escalated'
                    ELSE 'open'
                END AS status,
                EXTRACT(EPOCH FROM (NOW() - first_seen)) AS age_s
            FROM event_windows
        )
        SELECT * FROM lifecycle
        WHERE ($1 = 'all' OR status = $1)
        ORDER BY
            CASE status WHEN 'escalated' THEN 0 WHEN 'open' THEN 1 ELSE 2 END,
            peak_severity DESC,
            peak_confidence DESC
        LIMIT $2
    """, status, limit)

    events = []
    for r in rows:
        dur_s = int(r["duration_s"] or 0)
        age_s = int(r["age_s"] or 0)
        events.append({
            "event_type":       r["event_type"],
            "affected_prefix":  r["affected_prefix"],
            "origin_asn":       r["origin_asn"],
            "expected_asn":     r["expected_asn"],
            "status":           r["status"],
            "first_seen":       r["first_seen"].isoformat(),
            "last_seen":        r["last_seen"].isoformat(),
            "duration_s":       dur_s,
            "duration_human":   _fmt_duration(dur_s),
            "age_s":            age_s,
            "age_human":        _fmt_duration(age_s),
            "peak_confidence":  round(float(r["peak_confidence"]), 3),
            "peak_severity":    r["peak_severity"],
            "occurrence_count": r["occurrence_count"],
            "origin_name":      _asn_name(r["origin_asn"]),
            "origin_country":   _asn_country(r["origin_asn"]),
        })

    result = {
        "status_filter": status,
        "total":    len(events),
        "open":     sum(1 for e in events if e["status"] == "open"),
        "escalated":sum(1 for e in events if e["status"] == "escalated"),
        "resolved": sum(1 for e in events if e["status"] == "resolved"),
        "events":   events,
        "timestamp":datetime.now(tz=timezone.utc).isoformat(),
        "cached":   False,
    }
    try:
        redis = await get_redis()
        await redis.setex(cache_key, 30, json.dumps(result))
    except Exception:
        pass
    return result


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:    return f"{seconds}s"
    if seconds < 3600:  return f"{seconds//60}m {seconds%60}s"
    h = seconds // 3600; m = (seconds % 3600) // 60
    return f"{h}h {m}m"


@router.get("/as-path/{asn}")
async def as_path_hops(asn: int, limit: int = Query(20, ge=1, le=100)):
    """
    Return recent AS paths observed for a given origin ASN.
    Used by the globe to draw hop-by-hop path arcs.
    """
    pool = await get_pg_pool()
    rows = await pool.fetch("""
        SELECT DISTINCT ON (as_path)
            prefix, as_path, peer_asn,
            count(*) OVER (PARTITION BY as_path) AS frequency,
            max(time) OVER (PARTITION BY as_path) AS last_seen
        FROM bgp_updates
        WHERE origin_asn = $1
          AND as_path IS NOT NULL
          AND array_length(as_path, 1) >= 2
          AND time > NOW() - INTERVAL '30 minutes'
        ORDER BY as_path, time DESC
        LIMIT $2
    """, asn, limit)

    paths = []
    for r in rows:
        paths.append({
            "prefix":    r["prefix"],
            "as_path":   list(r["as_path"]),
            "hops":      len(r["as_path"]),
            "peer_asn":  r["peer_asn"],
            "frequency": r["frequency"],
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
        })

    return {"asn": asn, "paths": paths,
            "timestamp": datetime.now(tz=timezone.utc).isoformat()}
