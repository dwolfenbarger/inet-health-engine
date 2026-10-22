"""
collectors/rpki_lookup.py

Fast Redis-backed RPKI status lookup for use inside synchronous anomaly detection.

The RPKI collector writes validated prefix→ASN→status records to TimescaleDB.
This module maintains a hot Redis cache of those records and provides a
synchronous lookup that adds <1ms to anomaly detection per BGP update.

Cache key:  rpki:v2:{prefix}:{origin_asn}  →  "valid" | "invalid" | "not-found"
TTL:        at most 20 minutes from the validated snapshot; versioned local-validator keys

Falls back to "unknown" if Redis is unavailable or prefix not cached.
The collector-rpki container populates this cache via write_rpki_cache().
"""

import os
import redis

_r: redis.Redis | None = None
RPKI_CACHE_TTL = 600  # Legacy callers remain bounded to ten minutes


def _get_redis() -> redis.Redis:
    global _r
    if _r is None:
        url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        _r = redis.from_url(url, decode_responses=True, socket_timeout=0.5)
    return _r


def get_rpki_status_sync(prefix: str, origin_asn: int | None) -> str:
    """
    Synchronous RPKI status lookup from Redis cache.
    Returns: 'valid' | 'invalid' | 'not-found' | 'unknown'
    """
    if not origin_asn:
        return "unknown"
    try:
        r   = _get_redis()
        key = f"rpki:v2:{prefix}:{origin_asn}"
        val = r.get(key)
        return val if val else "unknown"
    except Exception:
        return "unknown"


def write_rpki_cache(prefix: str, origin_asn: int, status: str):
    """
    Write RPKI validation result to Redis cache.
    Called by the RPKI collector after each validation.
    """
    try:
        r   = _get_redis()
        key = f"rpki:v2:{prefix}:{origin_asn}"
        r.set(key, status, ex=RPKI_CACHE_TTL)
    except Exception:
        pass


def warm_rpki_cache_from_db(pool_sync) -> int:
    """Legacy public-endpoint cache warming is disabled; local snapshots populate v2 keys."""
    return 0
