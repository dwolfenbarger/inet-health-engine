"""
collectors/rpki_collector.py

RPKI (Resource Public Key Infrastructure) validation collector.
Tracks prefix origin validation state globally via Routinator REST API.

Sources:
  - Routinator REST API (RIPE's RPKI validator)
  - RIPE RPKI validator API (fallback)
  - Cloudflare RPKI API

Provides:
  - Per-prefix ROA validation state: valid / invalid / not-found
  - RPKI coverage trends by region and AS
  - Invalid prefix detection (origin AS mismatch)

Cadence: every 10 minutes

Writes to:
  - TimescaleDB: rpki_status table
  - Redis: raw.rpki stream
  - Enriches bgp_updates with rpki_status field
"""

import asyncio
import json
import signal
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.config import settings
from collectors.rpki_lookup import write_rpki_cache
from collectors.db import get_pg_pool

log = structlog.get_logger("rpki_collector")

RPKI_INTERVAL = 600  # 10 minutes

# Routinator public instance
ROUTINATOR_BASE = "https://rpki-validator.ripe.net"  # Cloudflare API deprecated

# RIPE RPKI validator API
RIPE_RPKI_BASE  = "https://rpki-validator.ripe.net/api"

# Sample of high-value prefixes to validate every cycle
# In Phase 3 this expands to the full BGP table seen by our collectors
SAMPLE_PREFIXES = [
    ("1.1.1.0/24",      13335),  # Cloudflare
    ("1.0.0.0/24",      13335),  # Cloudflare
    ("8.8.8.0/24",      15169),  # Google
    ("8.8.4.0/24",      15169),  # Google
    ("9.9.9.0/24",      19281),  # Quad9
    ("104.16.0.0/12",   13335),  # Cloudflare CDN
    ("13.32.0.0/15",    16509),  # AWS CloudFront
    ("151.101.0.0/16",  54113),  # Fastly
    ("2606:4700::/32",  13335),  # Cloudflare IPv6
    ("2001:4860::/32",  15169),  # Google IPv6
]

async def ensure_rpki_tables():
    """Create RPKI tables in TimescaleDB."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rpki_status (
                time        TIMESTAMPTZ NOT NULL,
                prefix      TEXT NOT NULL,
                origin_asn  INTEGER NOT NULL,
                status      TEXT NOT NULL,  -- valid | invalid | not-found
                roa_count   INTEGER DEFAULT 0,
                source      TEXT,
                max_length  INTEGER
            )
        """)
        try:
            await conn.execute(
                "SELECT create_hypertable('rpki_status', 'time', if_not_exists => TRUE)"
            )
        except Exception:
            pass
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS rpki_prefix_idx ON rpki_status (prefix, time DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS rpki_status_idx ON rpki_status (status, time DESC)"
        )
    log.info("rpki_tables_ready")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def validate_prefix_cloudflare(
    client: httpx.AsyncClient,
    prefix: str,
    origin_asn: int,
) -> dict:
    """
    Validate a prefix/ASN pair against Cloudflare's RPKI API.
    Returns validation status and ROA details.
    """
    try:
        resp = await client.get(
            f"{ROUTINATOR_BASE}/api/v1/validity/AS{origin_asn}/{prefix}",
            timeout=10,
        )
        resp.raise_for_status()
        data  = resp.json()
        state = data.get("validated_route", {}).get("validity", {})

        return {
            "prefix":     prefix,
            "origin_asn": origin_asn,
            "status":     state.get("state", "not-found"),
            "roa_count":  len(state.get("VRPs", {}).get("matched", [])),
            "source":     "ripe-rpki",
            "max_length": int(state.get("VRPs", {}).get("matched", [{}])[0].get("max_length", 0) or 0)
                          if state.get("VRPs", {}).get("matched") else None,
        }
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {
                "prefix":     prefix,
                "origin_asn": origin_asn,
                "status":     "not-found",
                "roa_count":  0,
                "source":     "ripe-rpki",
                "max_length": None,
            }
        raise
    except Exception as e:
        log.warning("rpki_cloudflare_error", prefix=prefix, asn=origin_asn, error=str(e))
        return {
            "prefix":     prefix,
            "origin_asn": origin_asn,
            "status":     "unknown",
            "roa_count":  0,
            "source":     "ripe-rpki-error",
            "max_length": None,
        }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def validate_prefix_ripe(
    client: httpx.AsyncClient,
    prefix: str,
    origin_asn: int,
) -> dict:
    """
    Validate prefix/ASN against RIPE RPKI validator (fallback).
    """
    try:
        resp = await client.get(
            f"{RIPE_RPKI_BASE}/v1/validity/AS{origin_asn}/{prefix}",
            timeout=10,
        )
        resp.raise_for_status()
        data   = resp.json()
        result = data.get("validated_route", {}).get("validity", {})

        return {
            "prefix":     prefix,
            "origin_asn": origin_asn,
            "status":     result.get("state", "not-found"),
            "roa_count":  len(result.get("VRPs", {}).get("matched", [])),
            "source":     "ripe-rpki",
            "max_length": None,
        }
    except Exception as e:
        log.warning("rpki_ripe_error", prefix=prefix, error=str(e))
        return {
            "prefix": prefix, "origin_asn": origin_asn,
            "status": "unknown", "roa_count": 0,
            "source": "ripe-rpki-error", "max_length": None,
        }

async def backfill_bgp_updates_rpki(results: list[dict]):
    """
    Update rpki_status column on recent bgp_updates rows
    where we now have validated state.
    Enriches existing BGP data retroactively.
    """
    pool = await get_pg_pool()
    now  = datetime.now(tz=timezone.utc)

    async with pool.acquire() as conn:
        for r in results:
            if r["status"] not in ("valid", "invalid"):
                continue
            await conn.execute("""
                UPDATE bgp_updates
                SET rpki_status = $1
                WHERE prefix     = $2
                  AND origin_asn = $3
                  AND time > NOW() - INTERVAL '1 hour'
                  AND rpki_status = 'unknown'
            """, r["status"], r["prefix"], r["origin_asn"])

    log.info("bgp_rpki_backfill_complete", count=len(results))


async def write_rpki_status(results: list[dict]):
    """Write RPKI validation results to TimescaleDB."""
    if not results:
        return

    pool = await get_pg_pool()
    now  = datetime.now(tz=timezone.utc)
    rows = [
        (
            now,
            r["prefix"],
            r["origin_asn"],
            r["status"],
            r.get("roa_count", 0),
            r.get("source"),
            r.get("max_length"),
        )
        for r in results
    ]

    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO rpki_status
                (time, prefix, origin_asn, status, roa_count, source, max_length)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
        """, rows)

    log.info("rpki_status_written", count=len(rows))


async def publish_rpki_to_redis(results: list[dict]):
    """Publish RPKI invalid findings to Redis alert stream."""
    invalids = [r for r in results if r["status"] == "invalid"]
    if not invalids:
        return

    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        for inv in invalids:
            await r.xadd("raw.rpki", {
                "prefix":     inv["prefix"],
                "origin_asn": str(inv["origin_asn"]),
                "status":     "invalid",
                "source":     inv.get("source", ""),
                "roa_count":  str(inv.get("roa_count", 0)),
                "time":       datetime.now(tz=timezone.utc).isoformat(),
            }, maxlen=2000)
        await r.aclose()
        log.info("rpki_invalids_published", count=len(invalids))
    except Exception as e:
        log.warning("rpki_redis_error", error=str(e))


async def run_collection_cycle():
    """One full RPKI validation cycle — validates live BGP prefixes from RIS stream."""
    await ensure_rpki_tables()

    pool = await get_pg_pool()
    # Prioritise active anomaly prefixes — that's where RPKI confidence matters most
    anom_rows = await pool.fetch("""
        SELECT DISTINCT affected_prefix AS prefix, origin_asn
        FROM bgp_anomalies
        WHERE time > NOW() - INTERVAL '10 minutes'
          AND origin_asn IS NOT NULL
          AND affected_prefix IS NOT NULL
          AND source LIKE 'ris/%%'
        LIMIT 400
    """)
    live_prefixes = [(r["prefix"], r["origin_asn"]) for r in anom_rows]
    # Fill remainder with high-activity prefixes
    remaining = 500 - len(live_prefixes)
    if remaining > 0:
        upd_rows = await pool.fetch("""
            SELECT prefix, origin_asn
            FROM bgp_updates
            WHERE time > NOW() - INTERVAL '10 minutes'
              AND origin_asn IS NOT NULL AND prefix IS NOT NULL
            GROUP BY prefix, origin_asn
            ORDER BY count(*) DESC LIMIT $1
        """, remaining)
        live_prefixes += [(r["prefix"], r["origin_asn"]) for r in upd_rows]
    if not live_prefixes:
        live_prefixes = SAMPLE_PREFIXES

    log.info("rpki_cycle_start", prefix_count=len(live_prefixes), source="live_ris")

    results = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        # Validate in batches of 20 concurrently to avoid hammering the validator
        for batch_start in range(0, len(live_prefixes), 50):
            batch = live_prefixes[batch_start:batch_start+50]
            tasks = [validate_prefix_cloudflare(client, pfx, asn) for pfx, asn in batch]
            raw = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(raw):
                if isinstance(r, dict):
                    results.append(r)
                else:
                    pfx, asn = batch[i]
                    fallback = await validate_prefix_ripe(client, pfx, asn)
                    results.append(fallback)
            await asyncio.sleep(0.2)  # polite but efficient

    # Write to Redis cache for synchronous anomaly detector
    for r in results:
        if r.get("status") and r.get("prefix") and r.get("origin_asn"):
            write_rpki_cache(r["prefix"], r["origin_asn"], r["status"])

    # Summary stats
    by_status = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    log.info("rpki_validation_complete", results=len(results), by_status=by_status)

    # Sequential writes with explicit error handling
    # backfill_bgp_updates_rpki removed — hit compressed bgp_updates decompression
    # limit, causing silent failure of write_rpki_status via return_exceptions=True
    try:
        await write_rpki_status(results)
    except Exception as e:
        log.error("rpki_db_write_failed", error=str(e))
    try:
        await publish_rpki_to_redis(results)
    except Exception as e:
        log.warning("rpki_redis_publish_failed", error=str(e))

    return results


_running = True


def _handle_shutdown(sig, frame):
    global _running
    _running = False


async def main():
    global _running
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    log.info("rpki_collector_starting", interval_s=RPKI_INTERVAL)
    await get_pg_pool()

    while _running:
        start = time.time()
        try:
            await run_collection_cycle()
        except Exception as e:
            log.error("rpki_cycle_error", error=str(e), exc_info=True)

        elapsed   = time.time() - start
        sleep_for = max(0, RPKI_INTERVAL - elapsed)
        log.info("rpki_cycle_sleep",
                 elapsed_s=round(elapsed, 1),
                 sleep_s=round(sleep_for, 1))
        await asyncio.sleep(sleep_for)


if __name__ == "__main__":
    import structlog
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ])
    asyncio.run(main())
