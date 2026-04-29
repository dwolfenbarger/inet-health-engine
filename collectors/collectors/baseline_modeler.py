"""
collectors/baseline_modeler.py

Phase 3 anomaly baseline modeling.
Builds statistical baselines for BGP update rates, anomaly counts,
and prefix stability â€” then computes z-scores for current observations.

Baselines computed:
  - BGP update rate per 5-min window (global + per-AS)
  - Anomaly count per hour (by type)
  - Prefix churn rate (announce/withdraw ratio)
  - AS path length distribution

Z-score interpretation:
  z < 1.5  â†’ normal
  z 1.5-2.5 â†’ elevated (watch)
  z 2.5-3.5 â†’ high (alert)
  z > 3.5  â†’ critical (page)

Stored in TimescaleDB as_health table + Redis for live dashboard.
"""

import asyncio
import json
import math
import signal
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import structlog

from collectors.config import settings
from collectors.db import get_pg_pool

log = structlog.get_logger("baseline_modeler")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Statistical helpers
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def std_dev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def z_score(value: float, mu: float, sigma: float) -> float:
    if sigma < 0.001:
        return 0.0
    return round((value - mu) / sigma, 3)


def severity_from_z(z: float) -> int:
    az = abs(z)
    if az > 3.5:   return 5
    if az > 2.5:   return 4
    if az > 1.5:   return 3
    if az > 0.8:   return 2
    return 1


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Baseline computation
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def compute_bgp_baseline(pool, lookback_hours: int = 6) -> dict:
    """
    Compute BGP baseline from as_health table (pre-aggregated, fast).
    Falls back to well-known constants for the RIS 50K/cycle architecture.
    """
    try:
        # Read from as_health (small table, pre-aggregated by baseline modeler itself)
        # Query actual measured values from bgp_updates in 30-min buckets
        # This gives us real withdrawal and prefix counts, not ratio-derived approximations.
        # Lookback of 6h = 12 x 30-min buckets for stable statistics.
        rows = await pool.fetch("""
            SELECT
                time_bucket('30 minutes', time)                                  AS bucket,
                count(*)                                                         AS updates,
                count(*) FILTER (WHERE change_type = 'announce')                 AS announces,
                count(*) FILTER (WHERE change_type = 'withdraw')                 AS withdrawals
            FROM bgp_updates
            WHERE time >= NOW() - INTERVAL '6 hours'
              AND time <  NOW()
              AND collector != 'stub-rrc00'
            GROUP BY bucket
            ORDER BY bucket DESC
            LIMIT 12
        """)

        if len(rows) >= 3:
            updates     = [float(r["updates"])     for r in rows if r["updates"]]
            announces   = [float(r["announces"])   for r in rows if r["announces"]]
            withdrawals = [float(r["withdrawals"]) for r in rows if r["withdrawals"]]

            # Enforce a minimum std to prevent z-score explosion from low variance periods.
            # 8% of mean is a reasonable floor for BGP update rate noise.
            u_mean = mean(updates)
            w_mean = mean(withdrawals)
            u_std  = max(std_dev(updates),     u_mean * 0.08)
            w_std  = max(std_dev(withdrawals), w_mean * 0.12)

            if updates:
                return {
                    "update_rate":     {"mean": round(u_mean,2), "std": round(u_std,2), "n": len(updates)},
                    "announce_rate":   {"mean": round(mean(announces),2), "std": round(max(std_dev(announces), mean(announces)*0.08),2)},
                    "withdrawal_rate": {"mean": round(w_mean,2), "std": round(w_std,2)},
                    # prefix_diversity removed from scoring - unreliable with 25K/cycle cap
                    "prefix_diversity":{"mean": 0.0, "std": 1.0},
                    "computed_at": datetime.now(tz=timezone.utc).isoformat(),
                    "window_hours": lookback_hours,
                    "source": "bgp_updates_measured",
                }
    except Exception as e:
        log.warning("bgp_baseline_error", error=str(e))

    # Bootstrap baseline: RIS 5 collectors Ã— ~10K updates/min = 150K/30min bucket
    # These are calibrated to the real observed RIS stream volumes
    log.info("baseline_using_bootstrap_constants")
    return {
        "update_rate":     {"mean": 690000.0, "std": 80000.0, "n": 0},
        "announce_rate":   {"mean": 632000.0, "std": 75000.0},
        "withdrawal_rate": {"mean":  58000.0, "std": 12000.0},
        "prefix_diversity":{"mean":  74000.0, "std": 12000.0},
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
        "window_hours": lookback_hours,
        "source": "bootstrap_constants",
    }


async def compute_anomaly_baseline(pool, lookback_hours: int = 24) -> dict:
    """
    Compute anomaly frequency baseline per hour.
    Detects when anomaly rates are statistically unusual.
    """
    try:
        rows = await pool.fetch("""
            SELECT
                time_bucket('1 hour', time) AS bucket,
                event_type,
                count(*)                    AS count,
                round(avg(confidence)::numeric, 3) AS avg_conf
            FROM bgp_anomalies
            WHERE time >= NOW() - INTERVAL '6 hours'
              AND time <  NOW()
              AND source LIKE 'ris/%'
            GROUP BY bucket, event_type
            ORDER BY bucket DESC
            LIMIT 36
        """)

        if not rows:
            return {}

        by_type: dict[str, list[int]] = defaultdict(list)
        for r in rows:
            by_type[r["event_type"]].append(r["count"])

        baseline = {}
        for event_type, counts in by_type.items():
            baseline[event_type] = {
                "hourly_mean": round(mean(counts), 2),
                "hourly_std":  round(std_dev(counts), 2),
                "total":       sum(counts),
                "n_hours":     len(counts),
            }

        return baseline

    except Exception as e:
        log.warning("anomaly_baseline_error", error=str(e))
        return {}

async def score_current_window(pool, baseline: dict) -> dict:
    """
    Score the most recent 5-min window against the baseline.
    Returns z-scores, severity levels, and alert flags.
    """
    try:
        row = await pool.fetchrow("""
            SELECT
                count(*)                         AS update_count,
                count(*) FILTER (WHERE change_type = 'announce') AS announces,
                count(*) FILTER (WHERE change_type = 'withdraw') AS withdrawals,
                count(DISTINCT prefix)            AS unique_prefixes,
                count(DISTINCT origin_asn)        AS unique_asns
            FROM bgp_updates
            WHERE time >= NOW() - INTERVAL '30 minutes'
              AND time <  NOW()
              AND collector != 'stub-rrc00'
        """)

        if not row or not baseline:
            return {}

        update_rate   = baseline.get("update_rate", {})
        withdraw_rate = baseline.get("withdrawal_rate", {})
        prefix_div    = baseline.get("prefix_diversity", {})

        current_updates     = row["update_count"] or 0
        current_withdrawals = row["withdrawals"]  or 0
        current_prefixes    = row["unique_prefixes"] or 0

        z_updates     = z_score(current_updates,     update_rate.get("mean", 0),   update_rate.get("std", 1))
        z_withdrawals = z_score(current_withdrawals, withdraw_rate.get("mean", 0), withdraw_rate.get("std", 1))
        # prefix_diversity excluded: capped at 25K/cycle makes unique_prefix count
        # non-representative of actual routing table diversity. Always z=0.
        z_prefixes    = 0.0

        # Health score: only penalise beyond 1.5σ (normal operational noise)
        # update_rate: max 25pt penalty (volume changes rarely indicate outage alone)
        # withdrawal_rate: max 40pt penalty (primary outage signal)
        def _penalty(z: float, weight: float, cap: float, dead_band: float = 1.5) -> float:
            excess = max(0.0, abs(z) - dead_band)
            return min(excess * weight, cap)

        penalties = (
            _penalty(z_updates,     weight=6,  cap=25) +
            _penalty(z_withdrawals, weight=10, cap=40)
        )
        health_score = round(max(10.0, 100.0 - penalties), 1)

        return {
            "timestamp":          datetime.now(tz=timezone.utc).isoformat(),
            "current": {
                "updates_30m":     current_updates,
                "withdrawals_30m": current_withdrawals,
                "unique_prefixes": current_prefixes,
                "unique_asns":    row["unique_asns"] or 0,
            },
            "z_scores": {
                "update_rate":    z_updates,
                "withdrawal_rate": z_withdrawals,
                "prefix_diversity": z_prefixes,
            },
            "severity": {
                "update_rate":    severity_from_z(z_updates),
                "withdrawal_rate": severity_from_z(z_withdrawals),
                "overall":        max(
                    severity_from_z(z_updates),
                    severity_from_z(z_withdrawals),
                    severity_from_z(z_prefixes),
                ),
            },
            "health_score": health_score,
            "alerts": [
                f"UPDATE_RATE_z{z_updates:.1f}"    if abs(z_updates) > 2.5 else None,
                f"WITHDRAWAL_z{z_withdrawals:.1f}" if abs(z_withdrawals) > 2.5 else None,
                None,  # prefix_diversity slot kept for API compat, always None
            ],
        }

    except Exception as e:
        log.warning("score_window_error", error=str(e))
        return {}


async def write_health_snapshot(pool, score: dict):
    """Write current health score to as_health table (global ASN=0 row)."""
    if not score:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO as_health
                    (time, asn, health_score, bgp_update_rate, anomaly_count)
                VALUES (NOW(), 0, $1, $2, $3)
            """,
                score.get("health_score", 0),
                float(score.get("current", {}).get("updates_30m", 0)),
                0,
            )
    except Exception as e:
        log.warning("health_write_error", error=str(e))


async def publish_health_to_redis(score: dict):
    """Push health snapshot to Redis for live dashboard."""
    if not score:
        return
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.set("health:global", json.dumps(score), ex=600)
        await r.xadd("raw.health", {
            "health_score": str(score.get("health_score", 0)),
            "severity":     str(score.get("severity", {}).get("overall", 1)),
            "z_updates":    str(score.get("z_scores", {}).get("update_rate", 0)),
        }, maxlen=1000)
        await r.aclose()
    except Exception as e:
        log.warning("health_redis_error", error=str(e))


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Main loop
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_running = True


def _handle_shutdown(sig, frame):
    global _running
    _running = False
    log.info("shutdown_received")


async def main():
    global _running
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    log.info("baseline_modeler_starting")
    pool = await get_pg_pool()

    # Recompute baseline every 30 minutes
    baseline: dict = {}
    last_baseline_compute: float = 0.0
    BASELINE_INTERVAL = 1800

    while _running:
        now = time.time()

        # Recompute baseline if stale
        if now - last_baseline_compute >= BASELINE_INTERVAL:
            log.info("computing_baseline")
            bgp_baseline   = await compute_bgp_baseline(pool)
            anom_baseline  = await compute_anomaly_baseline(pool)
            baseline = {**bgp_baseline, "anomalies": anom_baseline}
            last_baseline_compute = now
            log.info("baseline_computed",
                     update_mean=baseline.get("update_rate", {}).get("mean"),
                     withdrawal_mean=baseline.get("withdrawal_rate", {}).get("mean"))

        # Score current window every 5 minutes
        score = await score_current_window(pool, baseline)

        if score:
            log.info("health_score",
                     score=score.get("health_score"),
                     severity=score.get("severity", {}).get("overall"),
                     z_updates=score.get("z_scores", {}).get("update_rate"))

            await asyncio.gather(
                write_health_snapshot(pool, score),
                publish_health_to_redis(score),
                return_exceptions=True,
            )

        await asyncio.sleep(300)

    log.info("baseline_modeler_stopped")


if __name__ == "__main__":
    import structlog
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ])
    asyncio.run(main())
