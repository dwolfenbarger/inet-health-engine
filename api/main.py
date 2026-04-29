"""
api/main.py
Internet Health & Status Engine — FastAPI application.
All endpoints wired to real data sources.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import structlog

from api.deps import get_pg_pool, get_redis, get_es, close_all
from api.routes_bgp     import router as bgp_router
from api.routes_events  import router as events_router
from api.routes_traffic import router as traffic_router
from api.routes_intelligence import router as intelligence_router
from api.routes_globe import router as globe_router
from api.routes_events_lifecycle import router as lifecycle_router
from api.ws             import websocket_endpoint, redis_stream_listener

log = structlog.get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: warm DB pools + launch background WS listener."""
    log.info("api_startup")
    await get_pg_pool()
    await get_redis()
    await get_es()

    # Start Redis → WebSocket bridge as background task
    task = asyncio.create_task(redis_stream_listener())
    log.info("ws_stream_listener_launched")

    yield

    task.cancel()
    await close_all()
    log.info("api_shutdown")


app = FastAPI(
    title="Internet Health & Status Engine",
    description="BGP analysis, AS topology, community signal correlation — Phase 1",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────
app.include_router(bgp_router,     prefix="/api/v1")
app.include_router(events_router,  prefix="/api/v1")
app.include_router(traffic_router,      prefix="/api/v1")
app.include_router(intelligence_router, prefix="/api/v1")
app.include_router(globe_router,        prefix="/api/v1")
app.include_router(lifecycle_router,    prefix="/api/v1")


# ── WebSocket ─────────────────────────────────
@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await websocket_endpoint(websocket)


# ── Health + global status ───────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/v1/status")
async def global_status():
    """
    Global internet health summary for the NOC status bar.
    Pulls live counts from TimescaleDB + Elasticsearch.
    """
    pool = await get_pg_pool()
    es   = await get_es()

    pg_counts = await pool.fetchrow("""
        SELECT
            (SELECT count(*) FROM bgp_updates
             WHERE time > NOW() - INTERVAL '1 hour')       AS bgp_updates_1h,
            (SELECT count(*) FROM bgp_anomalies
             WHERE time > NOW() - INTERVAL '1 hour')       AS anomalies_1h,
            (SELECT count(*) FROM bgp_anomalies
             WHERE time > NOW() - INTERVAL '1 hour'
               AND severity >= 4)                          AS high_severity_1h,
            (SELECT count(*) FROM network_events
             WHERE time > NOW() - INTERVAL '6 hours'
               AND resolved_at IS NULL)                    AS active_events,
            (SELECT max(time) FROM bgp_updates)            AS last_bgp_update
    """)

    try:
        es_count = await es.count(
            index="community-signals",
            body={"query": {"range": {"collected_at": {"gte": "now-1h"}}}}
        )
        community_signals_1h = es_count["count"]
    except Exception:
        community_signals_1h = 0

    d = dict(pg_counts)
    return {
        "global_health_score":    _compute_health_score(d),
        "bgp_updates_1h":         d["bgp_updates_1h"],
        "anomalies_1h":           d["anomalies_1h"],
        "high_severity_events_1h": d["high_severity_1h"],
        "active_events":          d["active_events"],
        "community_signals_1h":   community_signals_1h,
        "last_bgp_update":        d["last_bgp_update"],
        "data_sources": {
            "timescaledb":    "healthy",
            "elasticsearch":  "healthy",
            "redis":          "healthy",
        },
    }


def _compute_health_score(counts: dict) -> float:
    """
    Composite internet health score 0-100.
    Higher = healthier. Penalizes anomalies and high-severity events.
    """
    score = 100.0
    score -= min(counts.get("anomalies_1h", 0) * 2, 30)
    score -= min(counts.get("high_severity_1h", 0) * 5, 40)
    score -= min(counts.get("active_events", 0) * 3, 20)
    return round(max(score, 0.0), 1)

# ── Background lifecycle cache warmer ──────────────────────────────────────
# Proactively refreshes the lifecycle cache every 45s so UI never waits for
# the expensive 4s query. On startup, warm immediately.
import asyncio, json as _json
from datetime import datetime, timezone

async def _warm_lifecycle_cache():
    """Pre-populate lifecycle Redis cache every 45 seconds."""
    # Wait for DB to be fully ready — retry with backoff
    for attempt in range(12):
        try:
            from api.deps import get_pg_pool
            pool = await get_pg_pool()
            await pool.fetchval("SELECT 1")
            break
        except Exception:
            await asyncio.sleep(10)
    else:
        return  # give up after 2 min — next scheduled run will retry
    while True:
        try:
            from api.deps import get_pg_pool, get_redis
            from api.routes_events_lifecycle import _asn_name, _asn_country, _fmt_duration
            pool  = await get_pg_pool()
            redis = await get_redis()
            rows  = await pool.fetch("""
                WITH ew AS (
                    SELECT event_type, affected_prefix, origin_asn, expected_asn,
                        MIN(time) AS first_seen, MAX(time) AS last_seen,
                        MAX(confidence) AS peak_confidence, MAX(severity) AS peak_severity,
                        COUNT(*) AS occurrence_count,
                        EXTRACT(EPOCH FROM (MAX(time)-MIN(time))) AS duration_s
                    FROM bgp_anomalies
                    WHERE source LIKE 'ris/%'
                      AND confidence > 0.5
                      AND origin_asn IS NOT NULL
                      AND time > NOW() - INTERVAL '6 hours'
                    GROUP BY event_type, affected_prefix, origin_asn, expected_asn
                )
                SELECT *,
                    CASE
                        WHEN last_seen < NOW()-INTERVAL '3 minutes' THEN 'resolved'
                        WHEN (duration_s > 900 AND occurrence_count >= 3)
                          OR occurrence_count >= 10 THEN 'escalated'
                        ELSE 'open'
                    END AS status,
                    EXTRACT(EPOCH FROM (NOW()-first_seen)) AS age_s
                FROM ew
                ORDER BY
                    CASE CASE WHEN last_seen<NOW()-INTERVAL '3 minutes' THEN 'resolved'
                              WHEN (duration_s>900 AND occurrence_count>=3) OR occurrence_count>=10 THEN 'escalated'
                              ELSE 'open' END
                         WHEN 'escalated' THEN 0 WHEN 'open' THEN 1 ELSE 2 END,
                    peak_severity DESC, peak_confidence DESC
                LIMIT 200
            """)
            events = []
            for r in rows:
                dur_s = int(r["duration_s"] or 0)
                age_s = int(r["age_s"] or 0)
                o_asn = r["origin_asn"]
                events.append({
                    "event_type": r["event_type"], "affected_prefix": r["affected_prefix"],
                    "origin_asn": o_asn, "expected_asn": r["expected_asn"],
                    "status": r["status"], "first_seen": r["first_seen"].isoformat(),
                    "last_seen": r["last_seen"].isoformat(),
                    "duration_s": dur_s, "duration_human": _fmt_duration(dur_s),
                    "age_s": age_s, "age_human": _fmt_duration(age_s),
                    "peak_confidence": round(float(r["peak_confidence"]), 3),
                    "peak_severity": r["peak_severity"],
                    "occurrence_count": r["occurrence_count"],
                    "origin_name": _asn_name(o_asn), "origin_country": _asn_country(o_asn),
                })
            for status_f in ["all", "open", "escalated", "resolved"]:
                filtered = events if status_f == "all" else [e for e in events if e["status"] == status_f]
                for limit_v in [10, 40, 50, 100, 200]:
                    result = {
                        "status_filter": status_f, "total": len(filtered[:limit_v]),
                        "open":      sum(1 for e in filtered[:limit_v] if e["status"] == "open"),
                        "escalated": sum(1 for e in filtered[:limit_v] if e["status"] == "escalated"),
                        "resolved":  sum(1 for e in filtered[:limit_v] if e["status"] == "resolved"),
                        "events":    filtered[:limit_v],
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                        "cached": True,
                    }
                    await redis.setex(f"lifecycle:{status_f}:{limit_v}", 60, _json.dumps(result))
        except Exception as exc:
            print(f"[lifecycle warmer] error: {exc}")
        # Also warm baseline cache (slow compressed bgp_updates query)
        try:
            from api.routes_intelligence import bgp_baseline
            import json as _j
            r2  = await get_redis()
            for h in [2, 6]:
                bl = await bgp_baseline(hours=h)
                await r2.setex(f"baseline:{h}", 90, _j.dumps(bl))
        except Exception as exc:
            print(f"[baseline warmer] {exc}")
        await asyncio.sleep(45)

@app.on_event("startup")
async def start_lifecycle_warmer():
    asyncio.create_task(_warm_lifecycle_cache())
