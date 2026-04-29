"""
collectors/cache.py — Redis-backed persistent caches for anomaly detection.

Keeps origin AS knowledge and flap history across 60-second RIS cycle
boundaries so we don't false-positive on every cycle restart.

Keys:
  bgp:origin:<prefix>    → "asn:first_seen_ts"   (TTL 7 days)
  bgp:flap:<prefix>      → JSON list of timestamps (TTL 10 minutes)
  bgp:withdrawal:<asn>   → integer count           (TTL 10 minutes)
"""

import json
import time
from typing import Optional

import structlog

from collectors.config import settings

log = structlog.get_logger("cache")

ORIGIN_TTL     = 7 * 86400   # 7 days — how long we remember a prefix origin
FLAP_TTL       = 600          # 10 min  — flap window
WITHDRAWAL_TTL = 600          # 10 min  — withdrawal surge window

_redis = None


async def get_redis():
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
        )
    return _redis


# ── Origin cache ──────────────────────────────────────────────────────────────

async def get_origin(prefix: str) -> Optional[tuple[int, float]]:
    """Return (origin_asn, first_seen_ts) for prefix, or None if unknown."""
    try:
        r = await get_redis()
        val = await r.get(f"bgp:origin:{prefix}")
        if val:
            asn_str, ts_str = val.split(":", 1)
            return int(asn_str), float(ts_str)
    except Exception as e:
        log.warning("cache_get_origin_error", prefix=prefix, error=str(e))
    return None


async def set_origin(prefix: str, asn: int, first_seen: float):
    """Record the known origin ASN for a prefix."""
    try:
        r = await get_redis()
        await r.set(f"bgp:origin:{prefix}", f"{asn}:{first_seen}", ex=ORIGIN_TTL)
    except Exception as e:
        log.warning("cache_set_origin_error", prefix=prefix, error=str(e))


async def bulk_get_origins(prefixes: list[str]) -> dict[str, tuple[int, float]]:
    """Fetch many origins in a single pipeline — used to warm in-memory cache."""
    result = {}
    if not prefixes:
        return result
    try:
        r = await get_redis()
        pipe = r.pipeline()
        for p in prefixes:
            pipe.get(f"bgp:origin:{p}")
        values = await pipe.execute()
        for prefix, val in zip(prefixes, values):
            if val:
                try:
                    asn_str, ts_str = val.split(":", 1)
                    result[prefix] = (int(asn_str), float(ts_str))
                except Exception:
                    pass
    except Exception as e:
        log.warning("cache_bulk_get_error", error=str(e))
    return result


async def bulk_set_origins(origins: dict[str, tuple[int, float]]):
    """Write many origins in a single pipeline — used to flush after cycle."""
    if not origins:
        return
    try:
        r = await get_redis()
        pipe = r.pipeline()
        for prefix, (asn, first_seen) in origins.items():
            pipe.set(f"bgp:origin:{prefix}", f"{asn}:{first_seen}", ex=ORIGIN_TTL)
        await pipe.execute()
        log.info("cache_origins_flushed", count=len(origins))
    except Exception as e:
        log.warning("cache_bulk_set_error", error=str(e))


# ── Flap tracker ──────────────────────────────────────────────────────────────

async def get_flap_timestamps(prefix: str) -> list[float]:
    """Return list of recent update timestamps for flap detection."""
    try:
        r = await get_redis()
        val = await r.get(f"bgp:flap:{prefix}")
        if val:
            return json.loads(val)
    except Exception as e:
        log.warning("cache_get_flap_error", prefix=prefix, error=str(e))
    return []


async def set_flap_timestamps(prefix: str, timestamps: list[float]):
    """Persist flap timestamps for a prefix."""
    try:
        r = await get_redis()
        # Only keep timestamps within the last flap window
        cutoff = time.time() - FLAP_TTL
        recent = [e for e in timestamps if (e[0] if isinstance(e, (list,tuple)) else e) > cutoff]
        if recent:
            await r.set(f"bgp:flap:{prefix}", json.dumps(recent), ex=FLAP_TTL)
        else:
            await r.delete(f"bgp:flap:{prefix}")
    except Exception as e:
        log.warning("cache_set_flap_error", prefix=prefix, error=str(e))


async def bulk_flush_flap_tracker(tracker: dict[str, list[float]]):
    """Flush entire in-memory flap tracker to Redis in one pipeline."""
    if not tracker:
        return
    try:
        r = await get_redis()
        cutoff = time.time() - FLAP_TTL
        pipe = r.pipeline()
        flushed = 0
        for prefix, timestamps in tracker.items():
            recent = [e for e in timestamps if (e[0] if isinstance(e, (list,tuple)) else e) > cutoff]
            if recent:
                pipe.set(f"bgp:flap:{prefix}", json.dumps(recent), ex=FLAP_TTL)
                flushed += 1
            else:
                pipe.delete(f"bgp:flap:{prefix}")
        await pipe.execute()
        log.info("cache_flap_flushed", prefixes=flushed)
    except Exception as e:
        log.warning("cache_flap_flush_error", error=str(e))


async def bulk_load_flap_tracker(prefixes: list[str]) -> dict[str, list[float]]:
    """Load flap timestamps for a set of prefixes from Redis."""
    result = {}
    if not prefixes:
        return result
    try:
        r = await get_redis()
        pipe = r.pipeline()
        for p in prefixes:
            pipe.get(f"bgp:flap:{p}")
        values = await pipe.execute()
        for prefix, val in zip(prefixes, values):
            if val:
                try:
                    result[prefix] = json.loads(val)
                except Exception:
                    pass
    except Exception as e:
        log.warning("cache_load_flap_error", error=str(e))
    return result


# ── Withdrawal counter ────────────────────────────────────────────────────────

async def get_withdrawal_count(asn: int) -> int:
    """Get rolling withdrawal count for an AS."""
    try:
        r = await get_redis()
        val = await r.get(f"bgp:withdrawal:{asn}")
        return int(val) if val else 0
    except Exception:
        return 0


async def increment_withdrawal_count(asn: int) -> int:
    """Increment and return rolling withdrawal count for an AS."""
    try:
        r = await get_redis()
        key = f"bgp:withdrawal:{asn}"
        count = await r.incr(key)
        await r.expire(key, WITHDRAWAL_TTL)
        return count
    except Exception:
        return 0


async def clear_withdrawal_counts():
    """Clear all withdrawal counters (call at cycle start, not end)."""
    try:
        r = await get_redis()
        keys = await r.keys("bgp:withdrawal:*")
        if keys:
            await r.delete(*keys)
    except Exception as e:
        log.warning("cache_clear_withdrawal_error", error=str(e))
