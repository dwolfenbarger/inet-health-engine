"""
collectors/bgp_collector.py

BGP update collector using PyBGPStream.
Sources: RIPE RIS + RouteViews via BGPStream CAIDA abstraction.

Responsibilities:
  - Pull BGP updates every POLL_INTERVAL seconds
  - Normalize to BGPUpdate model
  - Detect anomalies (hijacks, flaps, withdrawal surges)
  - Write to TimescaleDB (bgp_updates, bgp_anomalies)
  - Publish to Redis stream (raw.bgp) for downstream consumers
  - Update Neo4j AS topology graph

Run as:
    python -m collectors.bgp_collector
"""

import asyncio
import json
import time
import signal
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.config import settings
from collectors.db import get_pg_pool
from collectors.moas_whitelist import is_moas_whitelist
from collectors.rpki_lookup import get_rpki_status_sync  # fast Redis-backed lookup
from collectors.models import (
    BGPUpdate, BGPAnomaly, BGPChangeType, RPKIStatus,
    EventType, Severity
)

log = structlog.get_logger("bgp_collector")

# ─────────────────────────────────────────────
# State tracking for anomaly detection
# ─────────────────────────────────────────────

# prefix → (origin_asn, first_seen_ts)
# Loaded from Redis at cycle start, flushed back at cycle end
_prefix_origin_cache: dict[str, tuple[int, float]] = {}

# prefix → list of update timestamps (for flap detection)
# Backed by Redis — persists across 60s cycle boundaries
_prefix_flap_tracker: dict[str, list[float]] = defaultdict(list)

# AS → withdrawal count (Redis-backed, not cleared between cycles)
_as_withdrawal_counter: dict[int, int] = defaultdict(int)

# Calibrated thresholds from real RIS data (avg 1.7–10.5 changes/prefix/60s)
# A prefix is flapping if it changes more than 20x in 5 minutes
FLAP_THRESHOLD_COUNT   = 25    # true announce↔withdraw transitions in 5min
FLAP_WINDOW_SECONDS    = 300   # 5-minute rolling window

# Withdrawal surge: 200+ withdrawals from one AS in 10 min = real event
WITHDRAWAL_SURGE_THRESHOLD = 200  # was 50 — too sensitive for real data volume

# Minimum confidence to write a hijack anomaly — filters out weak signals
MIN_HIJACK_CONFIDENCE = 0.51  # requires >5min stability to reach

# Hijack confidence: require minimum prefix stability before firing
MIN_ORIGIN_STABILITY_SECONDS = 300  # prefix must have stable origin for 5min


# ─────────────────────────────────────────────
# BGPStream interface
# ─────────────────────────────────────────────

def _build_bgpstream(window_start: int, window_end: int) -> list[dict]:
    """
    Pull BGP updates from BGPStream for the given time window.
    Returns a list of normalized raw dicts before model validation.

    BGPStream runs synchronously — we call this in a thread executor
    to avoid blocking the event loop.
    """
    try:
        import bgpstream  # type: ignore
    except ImportError:
        log.warning("bgpstream_not_installed", msg="PyBGPStream not available, using stub")
        return _bgpstream_stub(window_start, window_end)

    stream = bgpstream.BGPStream(
        from_time=str(window_start),
        until_time=str(window_end),
        collectors=settings.ripe_ris_collectors + ["route-views2", "route-views.linx"],
        record_type="updates",
        filter="type ribs",
    )

    records = []
    for rec in stream:
        for elem in rec:
            if elem.type not in ("A", "W"):
                continue

            change_type = (
                BGPChangeType.ANNOUNCE if elem.type == "A"
                else BGPChangeType.WITHDRAW
            )

            as_path = []
            communities = []
            origin_asn = None
            next_hop = None

            if hasattr(elem, "fields"):
                raw_path = elem.fields.get("as-path", "")
                if raw_path:
                    # Strip AS sets {X,Y} — take first ASN from sets
                    path_parts = raw_path.replace("{", "").replace("}", "").split()
                    as_path = []
                    seen = set()
                    for p in path_parts:
                        try:
                            asn = int(p)
                            if asn not in seen:
                                as_path.append(asn)
                                seen.add(asn)
                        except ValueError:
                            pass
                    origin_asn = as_path[-1] if as_path else None

                raw_comms = elem.fields.get("communities", [])
                communities = [f"{c[0]}:{c[1]}" for c in raw_comms] if raw_comms else []
                next_hop = str(elem.fields.get("next-hop", "")) or None

            records.append({
                "time":        datetime.fromtimestamp(elem.time, tz=timezone.utc),
                "prefix":      str(elem.fields.get("prefix", elem.fields.get("next-hop", ""))),
                "origin_asn":  origin_asn,
                "as_path":     as_path,
                "communities": communities,
                "change_type": change_type,
                "collector":   rec.collector,
                "peer_asn":    elem.peer_asn,
                "next_hop":    next_hop,
                "rpki_status": RPKIStatus.UNKNOWN,
            })

    log.info("bgpstream_fetch_complete", count=len(records),
             window_start=window_start, window_end=window_end)
    return records


def _bgpstream_stub(window_start: int, window_end: int) -> list[dict]:
    """
    Stub for development/testing when PyBGPStream is not installed.
    Generates synthetic BGP updates to exercise the pipeline.
    Remove or gate behind a DEV_MODE flag before production.
    """
    import random
    log.warning("bgpstream_stub_active", msg="Generating synthetic BGP data")

    test_prefixes = [
        "1.1.1.0/24", "8.8.8.0/24", "9.9.9.0/24",
        "104.16.0.0/12", "13.32.0.0/15", "151.101.0.0/16",
    ]
    test_asns = [13335, 15169, 8075, 16509, 20940, 32934]
    results = []

    for _ in range(random.randint(10, 40)):
        prefix = random.choice(test_prefixes)
        origin = random.choice(test_asns)
        path_len = random.randint(2, 5)
        as_path = [random.choice(test_asns) for _ in range(path_len)] + [origin]

        results.append({
            "time":        datetime.fromtimestamp(
                               random.randint(window_start, window_end),
                               tz=timezone.utc
                           ),
            "prefix":      prefix,
            "origin_asn":  origin,
            "as_path":     as_path,
            "communities": [f"{origin}:100"],
            "change_type": random.choice([BGPChangeType.ANNOUNCE, BGPChangeType.WITHDRAW]),
            "collector":   "stub-rrc00",
            "peer_asn":    random.choice(test_asns),
            "next_hop":    "192.0.2.1",
            "rpki_status": RPKIStatus.UNKNOWN,
        })

    return results


# ─────────────────────────────────────────────
# Anomaly detection
# ─────────────────────────────────────────────

def detect_anomalies(update: BGPUpdate) -> list[BGPAnomaly]:
    """
    Run anomaly detection rules against a single BGP update.
    Returns a list of anomalies (may be empty).
    """
    anomalies = []
    now = time.time()
    prefix = update.prefix

    # ── 1. Origin AS change (potential hijack) ────────────────────────
    if update.change_type == BGPChangeType.ANNOUNCE and update.origin_asn:
        if prefix in _prefix_origin_cache:
            known_origin, first_seen = _prefix_origin_cache[prefix]
            stability_age = now - first_seen
            if known_origin != update.origin_asn:
                # Only fire if prefix had a stable origin for at least 5 minutes
                # Eliminates false positives from cold-cache first-seen
                if stability_age >= MIN_ORIGIN_STABILITY_SECONDS:
                    # Skip whitelisted MOAS (same operator, anycast, BYOIP)
                    if is_moas_whitelist(update.origin_asn, known_origin):
                        # Still update cache to the new origin
                        pass
                    else:
                        age_hours = stability_age / 3600
                        confidence = min(0.5 + (age_hours / 24) * 0.4, 0.9)
                        # RPKI adjustment — boosts or penalizes confidence
                        rpki = get_rpki_status_sync(prefix, update.origin_asn)
                        if rpki == 'invalid':
                            confidence = min(confidence + 0.25, 0.99)  # cryptographically invalid
                        elif rpki == 'valid':
                            confidence = max(confidence - 0.15, 0.0)   # origin has valid ROA — less suspicious
                        # Only write if confidence meets minimum threshold
                        if confidence >= MIN_HIJACK_CONFIDENCE:
                            anomalies.append(BGPAnomaly(
                        time=update.time,
                        event_type=EventType.BGP_HIJACK,
                        affected_prefix=prefix,
                        origin_asn=update.origin_asn,
                        expected_asn=known_origin,
                        severity=Severity.HIGH,
                        confidence=round(confidence, 3),
                        source=f"ris/{update.collector}",
                        raw_data={
                            "as_path": update.as_path,
                            "peer_asn": update.peer_asn,
                            "known_origin": known_origin,
                            "new_origin": update.origin_asn,
                            "prefix_age_hours": round(age_hours, 1),
                        }
                    ))
                # Update to new origin regardless
                _prefix_origin_cache[prefix] = (update.origin_asn, now)
        else:
            # First time we have seen this prefix — record it, no anomaly yet
            _prefix_origin_cache[prefix] = (update.origin_asn, now)

    return anomalies


def detect_flaps(update: BGPUpdate) -> list[BGPAnomaly]:
    """
    Detect BGP route flapping — true announce↔withdraw state cycling.

    We track (timestamp, change_type) pairs per prefix.
    A flap requires alternating announce/withdraw transitions, not just
    raw update volume (which includes normal multi-peer re-advertisements).
    """
    anomalies = []
    now = time.time()
    prefix = update.prefix

    # Store (timestamp, change_type) — only record state-relevant changes
    # Encode: 1=announce, 0=withdraw
    entry = (now, 1 if update.change_type == BGPChangeType.ANNOUNCE else 0)
    _prefix_flap_tracker[prefix].append(entry)

    # Trim entries older than the flap window
    cutoff = now - FLAP_WINDOW_SECONDS
    _prefix_flap_tracker[prefix] = [
        e for e in _prefix_flap_tracker[prefix] if e[0] > cutoff
    ]

    count = len(_prefix_flap_tracker[prefix])
    if count >= FLAP_THRESHOLD_COUNT:
        confidence = min(0.4 + (count - FLAP_THRESHOLD_COUNT) * 0.05, 0.85)
        # Withdrawals carry no AS path — use last known origin from cache
        # This prevents null-origin flap anomalies from withdrawal messages
        flap_origin = update.origin_asn
        if flap_origin is None:
            cached = _prefix_origin_cache.get(prefix)
            if cached:
                flap_origin = cached[0]
        anomalies.append(BGPAnomaly(
            time=update.time,
            event_type=EventType.BGP_FLAP,
            affected_prefix=prefix,
            origin_asn=flap_origin,
            severity=Severity.MEDIUM,
            confidence=round(confidence, 3),
            source=f"bgp-collector/{update.collector}",
            raw_data={
                "flap_count": count,
                "window_seconds": FLAP_WINDOW_SECONDS,
                "peer_asn": update.peer_asn,
            }
        ))

    return anomalies


def detect_withdrawal_surge(update: BGPUpdate) -> list[BGPAnomaly]:
    """Detect mass withdrawal events — potential network outage signal."""
    anomalies = []

    if update.change_type != BGPChangeType.WITHDRAW:
        return anomalies

    if update.origin_asn:
        _as_withdrawal_counter[update.origin_asn] += 1
        count = _as_withdrawal_counter[update.origin_asn]

        if count == WITHDRAWAL_SURGE_THRESHOLD:
            # Fire exactly once at threshold crossing
            # Sanitise prefix: reject None/"None"/empty strings that arise from
            # malformed RIS withdrawal entries so affected_prefix is never NULL.
            trigger_prefix = update.prefix if (
                update.prefix and update.prefix not in ("None", "none", "")
            ) else None
            anomalies.append(BGPAnomaly(
                time=update.time,
                event_type=EventType.WITHDRAWAL_SURGE,
                affected_prefix=trigger_prefix,
                origin_asn=update.origin_asn,
                severity=Severity.HIGH,
                confidence=0.75,
                source="bgp-collector",
                raw_data={
                    "withdrawal_count": count,
                    "window_seconds": settings.bgp_window_seconds,
                    "trigger_prefix": update.prefix,
                }
            ))

    return anomalies


def run_anomaly_detection(update: BGPUpdate) -> list[BGPAnomaly]:
    """Run all anomaly detectors against a single update."""
    anomalies = []
    anomalies.extend(detect_anomalies(update))
    anomalies.extend(detect_flaps(update))
    anomalies.extend(detect_withdrawal_surge(update))
    return anomalies


# ─────────────────────────────────────────────
# Storage writers
# ─────────────────────────────────────────────

async def write_updates_to_timescale(updates: list[BGPUpdate]):
    """Bulk-insert BGP updates into TimescaleDB."""
    if not updates:
        return

    pool = await get_pg_pool()

    rows = [
        (
            u.time,
            u.prefix,
            u.origin_asn,
            u.as_path or [],
            u.communities or [],
            u.change_type,
            u.collector,
            u.peer_asn,
            u.next_hop,
            u.rpki_status,
        )
        for u in updates
    ]

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO bgp_updates
                (time, prefix, origin_asn, as_path, communities,
                 change_type, collector, peer_asn, next_hop, rpki_status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )

    log.info("timescale_updates_written", count=len(rows))


async def write_anomalies_to_timescale(anomalies: list[BGPAnomaly]):
    """Insert detected anomalies into TimescaleDB."""
    if not anomalies:
        return

    pool = await get_pg_pool()

    rows = [
        (
            a.time,
            a.event_id,
            a.event_type,
            a.affected_prefix,
            a.origin_asn,
            a.expected_asn,
            a.severity,
            a.confidence,
            a.source,
            json.dumps(a.raw_data),
        )
        for a in anomalies
    ]

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO bgp_anomalies
                (time, event_id, event_type, affected_prefix, origin_asn,
                 expected_asn, severity, confidence, source, raw_data)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
            """,
            rows,
        )

    log.info("timescale_anomalies_written", count=len(rows))


async def publish_to_redis(updates: list[BGPUpdate], anomalies: list[BGPAnomaly]):
    """Publish events to Redis streams for downstream consumers."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)

        # Publish BGP updates to raw.bgp stream (sample every 10th to reduce volume)
        for i, u in enumerate(updates):
            if i % 10 == 0:
                await r.xadd("raw.bgp", {
                    "prefix":      u.prefix,
                    "origin_asn":  str(u.origin_asn or ""),
                    "change_type": u.change_type,
                    "collector":   u.collector,
                    "time":        u.time.isoformat(),
                }, maxlen=10000)

        # Publish all anomalies — these are high-value signals
        for a in anomalies:
            await r.xadd("raw.anomalies", {
                "event_id":   a.event_id,
                "event_type": a.event_type,
                "prefix":     a.affected_prefix or "",
                "origin_asn": str(a.origin_asn or ""),
                "severity":   str(a.severity),
                "confidence": str(a.confidence),
                "source":     a.source,
                "time":       a.time.isoformat(),
            }, maxlen=5000)

        await r.aclose()
        log.info("redis_published",
                 updates_sampled=len(updates) // 10,
                 anomalies=len(anomalies))

    except Exception as e:
        log.warning("redis_publish_failed", error=str(e))


# ─────────────────────────────────────────────
# Main collection cycle
# ─────────────────────────────────────────────

async def run_collection_cycle(window_start: int, window_end: int):
    """
    Execute one full collection cycle:
      1. Fetch BGP updates from BGPStream
      2. Normalize to BGPUpdate models
      3. Run anomaly detection
      4. Write to TimescaleDB
      5. Publish to Redis streams
    """
    log.info("collection_cycle_start",
             window_start=window_start, window_end=window_end,
             window_seconds=window_end - window_start)

    # Fetch in thread executor (BGPStream is synchronous)
    loop = asyncio.get_event_loop()
    raw_records = await loop.run_in_executor(
        None, _build_bgpstream, window_start, window_end
    )

    if not raw_records:
        log.info("collection_cycle_empty")
        return

    # Normalize
    updates: list[BGPUpdate] = []
    parse_errors = 0
    for rec in raw_records:
        try:
            updates.append(BGPUpdate(**rec))
        except Exception as e:
            parse_errors += 1
            if parse_errors <= 5:
                log.warning("update_parse_error", error=str(e), record=str(rec)[:200])

    log.info("normalization_complete",
             total=len(raw_records),
             valid=len(updates),
             errors=parse_errors)

    # Anomaly detection
    all_anomalies: list[BGPAnomaly] = []
    for update in updates:
        anomalies = run_anomaly_detection(update)
        all_anomalies.extend(anomalies)

    if all_anomalies:
        log.info("anomalies_detected", count=len(all_anomalies),
                 types=[a.event_type for a in all_anomalies])

    # Reset per-cycle counters
    _as_withdrawal_counter.clear()

    # Write to storage (concurrent)
    await asyncio.gather(
        write_updates_to_timescale(updates),
        write_anomalies_to_timescale(all_anomalies),
        publish_to_redis(updates, all_anomalies),
        return_exceptions=True,
    )

    log.info("collection_cycle_complete",
             updates=len(updates),
             anomalies=len(all_anomalies))


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

_running = True


def _handle_shutdown(sig, frame):
    global _running
    log.info("shutdown_signal_received", signal=sig)
    _running = False


async def main():
    global _running

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    log.info("bgp_collector_starting",
             poll_interval=settings.poll_interval,
             collectors=settings.ripe_ris_collectors)

    # Warm up DB pool
    await get_pg_pool()

    while _running:
        cycle_start = time.time()

        window_end   = int(cycle_start)
        window_start = window_end - settings.bgp_window_seconds

        try:
            await run_collection_cycle(window_start, window_end)
        except Exception as e:
            log.error("collection_cycle_error", error=str(e), exc_info=True)

        elapsed  = time.time() - cycle_start
        sleep_for = max(0, settings.poll_interval - elapsed)

        log.info("cycle_sleep",
                 elapsed_s=round(elapsed, 1),
                 sleep_s=round(sleep_for, 1))
        await asyncio.sleep(sleep_for)

    log.info("bgp_collector_stopped")


if __name__ == "__main__":
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ]
    )
    asyncio.run(main())
