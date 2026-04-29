"""
collectors/ripe_ris_collector.py — RIPE RIS Live streaming BGP collector.
VENGEANCE: 5 collectors, 60s window, no memory cap, 25K batch inserts.
"""
import asyncio, json, signal, time
from datetime import datetime, timezone
from collections import defaultdict

import httpx, structlog

from collectors.config import settings
from collectors.db import get_pg_pool
from collectors.models import BGPUpdate, BGPAnomaly, BGPChangeType, RPKIStatus
from collectors.bgp_collector import (
    run_anomaly_detection,
    _prefix_origin_cache, _prefix_flap_tracker, _as_withdrawal_counter,
)
from collectors.cache import (
    bulk_get_origins, bulk_set_origins,
    bulk_load_flap_tracker, bulk_flush_flap_tracker,
    clear_withdrawal_counts,
)

log = structlog.get_logger("ripe_ris_collector")

RIS_LIVE_URL  = "https://ris-live.ripe.net/v1/stream/"
RIS_CLIENT_ID = "inet-health-engine/0.1"

# 5 strategic collectors — global coverage, Pi-safe volume
# rrc00=Amsterdam rrc11=NYC rrc14=PaloAlto rrc16=Singapore rrc21=SaoPaulo
RIS_COLLECTORS = ["rrc00", "rrc11", "rrc14", "rrc16", "rrc21"]

# No memory cap on VENGEANCE (31GB RAM). Batch raised from Pi-era 5K to 25K.
BATCH_SIZE     = 25_000   # 25K rows/transaction is well within TimescaleDB capacity


def parse_ris_message(raw: dict) -> list[BGPUpdate]:
    """Parse one RIS-live message into BGPUpdate records."""
    updates = []
    data = raw.get("data", {})
    if data.get("type") != "UPDATE":
        return updates

    dt        = datetime.fromtimestamp(data.get("timestamp", time.time()), tz=timezone.utc)
    host      = data.get("host", "rrc00.ripe.net")
    collector = host.split(".")[0]
    peer_asn  = int(data.get("peer_asn", 0)) or None
    next_hop  = data.get("peer")

    # Deduplicate AS path prepends
    as_path, seen = [], set()
    for a in data.get("path", []):
        try:
            n = int(a)
            if n not in seen:
                as_path.append(n); seen.add(n)
        except (ValueError, TypeError):
            pass
    origin_asn = as_path[-1] if as_path else None

    communities = [
        f"{c[0]}:{c[1]}" for c in data.get("community", [])
        if isinstance(c, (list, tuple)) and len(c) == 2
    ]

    for ann in data.get("announcements", []):
        for prefix in ann.get("prefixes", []):
            try:
                updates.append(BGPUpdate(
                    time=dt, prefix=prefix, origin_asn=origin_asn,
                    as_path=as_path, communities=communities,
                    change_type=BGPChangeType.ANNOUNCE, collector=collector,
                    peer_asn=peer_asn, next_hop=next_hop or ann.get("next_hop"),
                    rpki_status=RPKIStatus.UNKNOWN,
                ))
            except Exception:
                pass

    for prefix in data.get("withdrawals", []):
        try:
            # Withdraw messages carry no AS path — use origin from same message
            # if present, otherwise fall back to Redis origin cache
            withdraw_origin = origin_asn
            if withdraw_origin is None:
                cached = _prefix_origin_cache.get(str(prefix))
                if cached:
                    withdraw_origin = cached[0]
            updates.append(BGPUpdate(
                time=dt, prefix=str(prefix), origin_asn=withdraw_origin,
                as_path=as_path, communities=communities,
                change_type=BGPChangeType.WITHDRAW, collector=collector,
                peer_asn=peer_asn, next_hop=next_hop,
                rpki_status=RPKIStatus.UNKNOWN,
            ))
        except Exception:
            pass

    return updates


async def stream_ris_live(window_seconds: int) -> list[BGPUpdate]:
    """Stream RIS-live for window_seconds, return all parsed updates for the full window."""
    updates: list[BGPUpdate] = []
    deadline   = time.time() + window_seconds
    msg_count  = 0

    params = {"format": "json", "client": RIS_CLIENT_ID}

    log.info("ris_stream_start", collectors=RIS_COLLECTORS, window_s=window_seconds)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(
            connect=10.0, read=window_seconds + 30, write=10.0, pool=10.0
        )) as client:
            async with client.stream(
                "GET", RIS_LIVE_URL, params=params,
                headers={"User-Agent": RIS_CLIENT_ID}
            ) as resp:
                resp.raise_for_status()
                log.info("ris_stream_connected")

                async for line in resp.aiter_lines():
                    if time.time() >= deadline:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if raw.get("type") != "ris_message":
                        continue

                    # Filter to our 5 collectors only
                    host = raw.get("data", {}).get("host", "")
                    if host.split(".")[0] not in RIS_COLLECTORS:
                        continue

                    parsed = parse_ris_message(raw)
                    msg_count += 1

                    updates.extend(parsed)

    except httpx.TimeoutException:
        log.info("ris_stream_window_complete")
    except Exception as e:
        log.error("ris_stream_error", error=str(e))

    log.info("ris_stream_done",
             messages=msg_count, updates=len(updates))
    return updates


async def write_updates_batched(pool, updates: list[BGPUpdate], rpki_map: dict | None = None):
    """Write BGP updates in BATCH_SIZE chunks to avoid huge single transactions."""
    total = 0
    for i in range(0, len(updates), BATCH_SIZE):
        batch = updates[i:i + BATCH_SIZE]
        try:
            async with pool.acquire() as conn:
                await conn.executemany("""
                    INSERT INTO bgp_updates
                        (time, prefix, origin_asn, as_path, communities,
                         change_type, collector, peer_asn, next_hop, rpki_status)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    ON CONFLICT DO NOTHING
                """, [
                    (u.time, u.prefix, u.origin_asn,
                     u.as_path, u.communities,
                     str(u.change_type), u.collector,
                     u.peer_asn, u.next_hop,
                     (rpki_map or {}).get((u.prefix, u.origin_asn), str(u.rpki_status) if u.rpki_status else "unknown"))
                    for u in batch
                ])
            total += len(batch)
        except Exception as e:
            log.warning("batch_write_error", batch=i, error=str(e))
    log.info("updates_written", total=total)


async def write_anomalies_batched(pool, anomalies: list[BGPAnomaly]):
    """Write anomalies in batches."""
    for i in range(0, len(anomalies), BATCH_SIZE):
        batch = anomalies[i:i + BATCH_SIZE]
        try:
            async with pool.acquire() as conn:
                await conn.executemany("""
                    INSERT INTO bgp_anomalies
                        (time, event_id, event_type, affected_prefix,
                         origin_asn, expected_asn, severity, confidence, source)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT DO NOTHING
                """, [
                    (a.time, str(a.event_id), str(a.event_type),
                     a.affected_prefix, a.origin_asn, a.expected_asn,
                     a.severity, a.confidence,
                     f"ris/{a.affected_prefix or 'global'}")
                    for a in batch
                ])
        except Exception as e:
            log.warning("anomaly_write_error", batch=i, error=str(e))


async def publish_ris_to_redis(updates: list[BGPUpdate], anomalies: list[BGPAnomaly]):
    """Publish summary stats to Redis streams (not all records — just events)."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)

        # Publish high-severity anomalies only
        for a in anomalies:
            if a.severity >= 3:
                await r.xadd("raw.anomalies", {
                    "event_type":      str(a.event_type),
                    "affected_prefix": a.affected_prefix or "",
                    "origin_asn":      str(a.origin_asn or ""),
                    "severity":        str(a.severity),
                    "confidence":      str(round(a.confidence, 3)),
                }, maxlen=2000)

        # Publish a summary of this cycle
        ann = sum(1 for u in updates if u.change_type == BGPChangeType.ANNOUNCE)
        wdr = sum(1 for u in updates if u.change_type == BGPChangeType.WITHDRAW)
        await r.xadd("raw.bgp", {
            "total":       str(len(updates)),
            "announces":   str(ann),
            "withdrawals": str(wdr),
            "unique_pfx":  str(len({u.prefix for u in updates})),
            "unique_asns": str(len({u.origin_asn for u in updates if u.origin_asn})),
        }, maxlen=500)

        await r.aclose()
    except Exception as e:
        log.warning("redis_publish_error", error=str(e))


async def run_collection_cycle(window_seconds: int):
    """Full cycle: load cache → stream → detect → batch write → flush cache → publish."""
    log.info("ris_cycle_start", window_s=window_seconds,
             collectors=RIS_COLLECTORS)
    start = time.time()
    pool  = await get_pg_pool()

    updates = await stream_ris_live(window_seconds)
    if not updates:
        log.info("ris_cycle_empty")
        return

    # ── Warm cache for prefixes not yet in memory ─────────────────────
    # Fetch Redis origins for any prefix we haven't seen before
    # This preserves first_seen timestamps across restarts
    unseen = list({
        u.prefix for u in updates
        if u.prefix not in _prefix_origin_cache
    })
    if unseen:
        warmed = await bulk_get_origins(unseen)
        _prefix_origin_cache.update(warmed)
        if warmed:
            log.info("cache_incremental_warm", new_prefixes=len(warmed))

    # ── Anomaly detection — one anomaly per (prefix, event_type) per cycle ──
    # Deduplication prevents writing thousands of records for the same event

    # RPKI batch enrichment - pipeline-fetch from Redis in a single round-trip.
    # Builds a {(prefix, origin_asn): status} dict passed to write_updates_batched.
    # BGPUpdate is immutable (Pydantic v1), so we never mutate models - we pass the
    # dict alongside the updates and apply it at INSERT time.
    _rpki_map: dict[tuple, str] = {}
    try:
        import redis.asyncio as _aioredis
        _rr = _aioredis.from_url(settings.redis_url, decode_responses=True)
        _announces = [u for u in updates if u.change_type == BGPChangeType.ANNOUNCE and u.origin_asn]
        if _announces:
            pipe = _rr.pipeline()
            for u in _announces:
                pipe.get(f"rpki:{u.prefix}:{u.origin_asn}")
            _results = await pipe.execute()
            hits = 0
            for u, status in zip(_announces, _results):
                if status:
                    _rpki_map[(u.prefix, u.origin_asn)] = status
                    hits += 1
        await _rr.aclose()
        log.info("rpki_inline_enriched",
                 announces=len(_announces) if _announces else 0,
                 cache_hits=hits if _announces else 0)
    except Exception as _e:
        log.warning("rpki_inline_enrichment_failed", error=str(_e))
    all_anomalies: list[BGPAnomaly] = []
    for u in updates:
        all_anomalies.extend(run_anomaly_detection(u))

    # Deduplicate: keep highest-confidence anomaly per (prefix, event_type)
    seen: dict[tuple, BGPAnomaly] = {}
    for a in all_anomalies:
        key = (a.affected_prefix or "", str(a.event_type), a.origin_asn or 0)
        if key not in seen or a.confidence > seen[key].confidence:
            seen[key] = a
    anomalies = list(seen.values())
    log.info("ris_anomaly_dedup",
             raw=len(all_anomalies), deduped=len(anomalies))

    ann = sum(1 for u in updates if u.change_type == BGPChangeType.ANNOUNCE)
    wdr = sum(1 for u in updates if u.change_type == BGPChangeType.WITHDRAW)
    log.info("ris_cycle_stats",
             updates=len(updates), announces=ann, withdrawals=wdr,
             unique_prefixes=len({u.prefix for u in updates}),
             unique_asns=len({u.origin_asn for u in updates if u.origin_asn}),
             anomalies=len(anomalies))

    # RIPE Stat bgp-state enrichment: query routing table to confirm anomalous prefixes.
    # Adjusts confidence +0.15 if routing table corroborates detected origin, -0.10 if not.
    # Cap 30 prefixes/cycle to respect stat.ripe.net rate limits.
    _quality_pre = [a for a in anomalies if a.severity >= 2 and a.confidence >= 0.5]
    _anom_pfxs   = list({a.affected_prefix for a in _quality_pre
                          if a.affected_prefix and "/" in a.affected_prefix})[:30]
    _bgp_state: dict = {}
    if _anom_pfxs:
        try:
            import httpx as _hx
            async with _hx.AsyncClient(timeout=8, follow_redirects=True) as _hxc:
                _raw = await asyncio.gather(
                    *[_hxc.get(
                        "https://stat.ripe.net/data/bgp-state/data.json",
                        params={"resource": p, "rrcs": "0,5,11,14,16,21"}
                      ) for p in _anom_pfxs],
                    return_exceptions=True
                )
            for pfx, resp in zip(_anom_pfxs, _raw):
                if isinstance(resp, Exception): continue
                try:
                    peers = resp.json().get("data", {}).get("bgp_state", [])
                    if peers:
                        _bgp_state[pfx] = {
                            "peer_count":    len(peers),
                            "seen_origins":  list({p["path"][-1] for p in peers
                                                   if isinstance(p.get("path"), list) and p["path"]}),
                        }
                except Exception:
                    pass
            log.info("bgp_state_enriched", prefixes=len(_bgp_state))
        except Exception as _e:
            log.warning("bgp_state_enrichment_failed", error=str(_e))

    quality_anomalies = []
    for _a in _quality_pre:
        _state = _bgp_state.get(_a.affected_prefix or "")
        if _state and _a.origin_asn:
            _origs = set(_state["seen_origins"])
            _prs   = _state["peer_count"]
            if _a.origin_asn in _origs and _prs >= 5:
                _a = _a.copy(update={"confidence": round(min(_a.confidence + 0.15, 0.98), 3)})
            elif _a.origin_asn not in _origs and _prs >= 5:
                _a = _a.copy(update={"confidence": round(max(_a.confidence - 0.10, 0.10), 3)})
        quality_anomalies.append(_a)

    # Write in batches, publish summary
    await write_updates_batched(pool, updates, _rpki_map)
    # Only persist quality anomalies — skip very low confidence noise
    await write_anomalies_batched(pool, quality_anomalies)

    # ── Flush updated caches back to Redis ────────────────────────────
    await asyncio.gather(
        bulk_set_origins(_prefix_origin_cache),
        bulk_flush_flap_tracker(_prefix_flap_tracker),
        publish_ris_to_redis(updates, anomalies),
        return_exceptions=True,
    )

    log.info("ris_cycle_complete",
             updates=len(updates), anomalies=len(anomalies),
             elapsed_s=round(time.time() - start, 1))


_running = True

def _handle_shutdown(sig, frame):
    global _running
    log.info("shutdown_signal_received", signal=sig)
    _running = False


async def main():
    global _running
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    log.info("ripe_ris_collector_starting",
             window_s=settings.bgp_window_seconds,
             collectors=RIS_COLLECTORS)
    await get_pg_pool()

    while _running:
        try:
            await run_collection_cycle(settings.bgp_window_seconds)
        except Exception as e:
            log.error("cycle_error", error=str(e))
            await asyncio.sleep(30)

    log.info("ripe_ris_collector_stopped")


if __name__ == "__main__":
    import structlog
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ])
    asyncio.run(main())
