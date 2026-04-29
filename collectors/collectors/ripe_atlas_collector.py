"""
collectors/ripe_atlas_collector.py

RIPE Atlas measurement collector.
Pulls active measurement results from RIPE Atlas API.
Provides real latency topology — RTT between probes and targets.

Sources:
  - RIPE Atlas REST API v4
  - Built-in measurements: traceroutes, DNS, ping
  - Global probe network (~10,000 active probes)

Cadence: every 10 minutes (measurements update slowly)

Writes to:
  - TimescaleDB: atlas_measurements table
  - Redis: raw.atlas stream
"""

import asyncio
import json
import signal
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.config import settings
from collectors.db import get_pg_pool

log = structlog.get_logger("ripe_atlas")

ATLAS_BASE     = "https://atlas.ripe.net/api/v2"
ATLAS_INTERVAL = 600   # 10 minutes

# RIPE Atlas built-in measurement IDs
# Only DNS measurements are publicly accessible without API key from this host.
# DNS RTT provides excellent network health signal — DNS resolution to root
# servers is one of the first things to degrade when BGP routing breaks.
BUILTIN_MEASUREMENTS = {
    "dns_root_k":     10001,  # DNS to k-root  (193.0.14.129) - verified 2026-04-28
    "dns_root_f":     10004,  # DNS to f-root  (192.5.5.241)  - 10002 was 404
    "dns_root_m":     10005,  # DNS to m-root  (192.36.148.17) - 10003 was 404
    "ping_root_k":     1001,  # Ping to k-root
    "tracert_root_k":  5001,  # Traceroute to k-root
}

# Anchor ASes to query routing state for
TARGET_PREFIXES = [
    "1.1.1.0/24",     # Cloudflare DNS
    "8.8.8.0/24",     # Google DNS
    "9.9.9.0/24",     # Quad9
    "208.67.222.0/24", # OpenDNS
]

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
async def fetch_measurement_results(
    client: httpx.AsyncClient,
    msm_id: int,
    hours_back: int = 1,
) -> list[dict]:
    """
    Fetch recent results for a RIPE Atlas measurement.
    Returns normalized result records.
    """
    start_ts = int((datetime.now(tz=timezone.utc) - timedelta(hours=hours_back)).timestamp())

    try:
        resp = await client.get(
            f"{ATLAS_BASE}/measurements/{msm_id}/results/",
            params={
                "start":  start_ts,
                "format": "json",
                "limit":  500,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            log.warning("atlas_measurement_not_found", msm_id=msm_id)
            return []
        raise
    except Exception as e:
        log.warning("atlas_fetch_error", msm_id=msm_id, error=str(e))
        return []


async def fetch_probe_anchors(client: httpx.AsyncClient) -> list[dict]:
    """
    Fetch list of RIPE Atlas anchor probes.
    Anchors are well-connected probes used for stable measurements.
    Returns probe metadata including ASN, country, coordinates.
    """
    try:
        resp = await client.get(
            f"{ATLAS_BASE}/anchors/",
            params={"format": "json", "page_size": 100},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        anchors = []
        for a in data.get("results", []):
            probe = a.get("probe", {})
            anchors.append({
                "anchor_id":  a.get("id"),
                "probe_id":   probe.get("id"),
                "asn_v4":     probe.get("asn_v4"),
                "asn_v6":     probe.get("asn_v6"),
                "country":    probe.get("country_code"),
                "lat":        probe.get("latitude"),
                "lon":        probe.get("longitude"),
                "status":     probe.get("status", {}).get("name"),
                "address_v4": probe.get("address_v4"),
            })
        log.info("atlas_anchors_fetched", count=len(anchors))
        return anchors
    except Exception as e:
        log.warning("atlas_anchors_error", error=str(e))
        return []


def parse_ping_result(result: dict) -> Optional[dict]:
    """Extract RTT stats from a ping measurement result."""
    avg_rtt = result.get("avg")
    if avg_rtt is None:
        # Try computing from result list
        raw_result = result.get("result", [])
        if not isinstance(raw_result, list): raw_result = []
        rtts = [r.get("rtt") for r in raw_result if isinstance(r, dict) and r.get("rtt")]
        avg_rtt = sum(rtts) / len(rtts) if rtts else None

    if avg_rtt is None:
        return None

    return {
        "time":        datetime.fromtimestamp(result.get("timestamp", 0), tz=timezone.utc),
        "probe_id":    result.get("prb_id"),
        "probe_asn":   result.get("from"),
        "target":      result.get("dst_addr"),
        "avg_rtt_ms":  round(avg_rtt, 3),
        "min_rtt_ms":  result.get("min"),
        "max_rtt_ms":  result.get("max"),
        "packet_loss": result.get("loss", 0),
        "msm_type":    "ping",
    }


def parse_dns_result(result: dict) -> Optional[dict]:
    """Extract DNS resolution time from a DNS measurement result."""
    answers = result.get("result", {})
    rt = answers.get("rt")   # Response time in ms
    if rt is None:
        return None

    return {
        "time":         datetime.fromtimestamp(result.get("timestamp", 0), tz=timezone.utc),
        "probe_id":     result.get("prb_id"),
        "probe_asn":    result.get("from"),
        "target":       result.get("dst_addr"),
        "avg_rtt_ms":   round(rt, 3),
        "min_rtt_ms":   rt,
        "max_rtt_ms":   rt,
        "packet_loss":  0,
        "msm_type":     "dns",
    }


def parse_traceroute_result(result: dict) -> list[dict]:
    """
    Extract per-hop RTT from a traceroute measurement result.
    Returns one record per hop with latency data.
    Handles both dict hops and string hops (error/timeout entries).
    """
    hops_data = []
    raw_hops = result.get("result", [])
    if not isinstance(raw_hops, list):
        return hops_data

    for hop in raw_hops:
        # Guard: some Atlas responses return strings ("*") for non-responding hops
        if not isinstance(hop, dict):
            continue
        hop_idx = hop.get("hop", 0)
        inner   = hop.get("result", [])
        if not isinstance(inner, list):
            continue

        rtts = []
        hop_addr = None
        for probe in inner:
            if not isinstance(probe, dict):
                continue
            rtt = probe.get("rtt")
            if isinstance(rtt, (int, float)) and rtt > 0:
                rtts.append(float(rtt))
            if not hop_addr:
                hop_addr = probe.get("from")

        if rtts:
            hops_data.append({
                "time":       datetime.fromtimestamp(result.get("timestamp", 0), tz=timezone.utc),
                "probe_id":   result.get("prb_id"),
                "target":     result.get("dst_addr"),
                "hop_index":  hop_idx,
                "hop_addr":   hop_addr,
                "avg_rtt_ms": round(sum(rtts) / len(rtts), 3),
                "msm_type":   "traceroute",
            })
    return hops_data

async def ensure_atlas_tables():
    """Create RIPE Atlas tables in TimescaleDB if they don't exist."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS atlas_measurements (
                time         TIMESTAMPTZ NOT NULL,
                probe_id     INTEGER,
                probe_asn    TEXT,
                target       TEXT,
                avg_rtt_ms   REAL,
                min_rtt_ms   REAL,
                max_rtt_ms   REAL,
                packet_loss  REAL,
                msm_type     TEXT,
                msm_id       INTEGER,
                hop_index    INTEGER,
                hop_addr     TEXT
            )
        """)
        # Create hypertable if not already one
        try:
            await conn.execute(
                "SELECT create_hypertable('atlas_measurements', 'time', if_not_exists => TRUE)"
            )
        except Exception:
            pass  # Already a hypertable
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS atlas_target_idx ON atlas_measurements (target, time DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS atlas_probe_idx ON atlas_measurements (probe_id, time DESC)"
        )
    log.info("atlas_tables_ready")


async def write_atlas_results(results: list[dict], msm_id: int):
    """Bulk insert Atlas measurement results to TimescaleDB."""
    if not results:
        return

    pool = await get_pg_pool()
    rows = [
        (
            r.get("time", datetime.now(tz=timezone.utc)),
            r.get("probe_id"),
            str(r.get("probe_asn", "")),
            r.get("target"),
            r.get("avg_rtt_ms"),
            r.get("min_rtt_ms"),
            r.get("max_rtt_ms"),
            r.get("packet_loss", 0),
            r.get("msm_type"),
            msm_id,
            r.get("hop_index"),
            r.get("hop_addr"),
        )
        for r in results
    ]

    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO atlas_measurements
                (time, probe_id, probe_asn, target, avg_rtt_ms, min_rtt_ms,
                 max_rtt_ms, packet_loss, msm_type, msm_id, hop_index, hop_addr)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        """, rows)

    log.info("atlas_results_written", count=len(rows), msm_id=msm_id)


async def publish_atlas_to_redis(latency_summary: dict):
    """Publish latency summary to Redis for the live feed."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.xadd("raw.atlas", {
            "targets":        json.dumps(list(latency_summary.keys())),
            "avg_rtts":       json.dumps({k: v.get("avg_rtt_ms") for k, v in latency_summary.items()}),
            "probe_count":    str(sum(v.get("probe_count", 0) for v in latency_summary.values())),
            "time":           datetime.now(tz=timezone.utc).isoformat(),
        }, maxlen=1000)
        await r.aclose()
    except Exception as e:
        log.warning("atlas_redis_error", error=str(e))


async def run_collection_cycle():
    """One full RIPE Atlas collection cycle."""
    log.info("atlas_cycle_start")
    await ensure_atlas_tables()

    all_results = []
    latency_summary = {}

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # Fetch ping measurement results
        for name, msm_id in BUILTIN_MEASUREMENTS.items():
            raw = await fetch_measurement_results(client, msm_id, hours_back=1)
            parsed = []

            if "ping" in name:
                for r in raw:
                    p = parse_ping_result(r)
                    if p:
                        p["msm_id"] = msm_id
                        parsed.append(p)
            elif "dns" in name:
                for r in raw:
                    p = parse_dns_result(r)
                    if p:
                        p["msm_id"] = msm_id
                        parsed.append(p)
            elif "tracert" in name:
                for r in raw:
                    hops = parse_traceroute_result(r)
                    for h in hops:
                        h["msm_id"] = msm_id
                    parsed.extend(hops)

            if parsed:
                await write_atlas_results(parsed, msm_id)
                all_results.extend(parsed)

                # Build latency summary per target
                ping_results = [p for p in parsed if p.get("msm_type") == "ping"]
                if ping_results:
                    target = ping_results[0].get("target", name)
                    rtts = [p["avg_rtt_ms"] for p in ping_results if p.get("avg_rtt_ms")]
                    if rtts:
                        latency_summary[target] = {
                            "avg_rtt_ms":  round(sum(rtts) / len(rtts), 2),
                            "min_rtt_ms":  round(min(rtts), 2),
                            "max_rtt_ms":  round(max(rtts), 2),
                            "probe_count": len(rtts),
                        }

            log.info("atlas_msm_processed", name=name, msm_id=msm_id, results=len(parsed))

    if latency_summary:
        await publish_atlas_to_redis(latency_summary)

    log.info("atlas_cycle_complete",
             total_results=len(all_results),
             targets_with_latency=len(latency_summary))
    return latency_summary


_running = True


def _handle_shutdown(sig, frame):
    global _running
    _running = False
    log.info("atlas_shutdown_signal")


async def main():
    global _running
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    log.info("ripe_atlas_collector_starting", interval_s=ATLAS_INTERVAL)
    await get_pg_pool()

    while _running:
        start = time.time()
        try:
            await run_collection_cycle()
        except Exception as e:
            log.error("atlas_cycle_error", error=str(e), exc_info=True)

        elapsed   = time.time() - start
        sleep_for = max(0, ATLAS_INTERVAL - elapsed)
        log.info("atlas_cycle_sleep", elapsed_s=round(elapsed, 1), sleep_s=round(sleep_for, 1))
        await asyncio.sleep(sleep_for)

    log.info("ripe_atlas_collector_stopped")


if __name__ == "__main__":
    import structlog
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ])
    asyncio.run(main())
