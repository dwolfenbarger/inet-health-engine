"""
collectors/rpki_lookup.py

Fast Redis-backed RPKI status lookup for use inside synchronous anomaly detection.

The RPKI collector writes validated prefix→ASN→status records to TimescaleDB.
This module maintains a hot Redis cache of those records and provides a
synchronous lookup that adds <1ms to anomaly detection per BGP update.

Cache key:  rpki:{prefix}:{origin_asn}  →  "valid" | "invalid" | "not-found"
TTL:        4 hours (RPKI state is slow-changing)

Falls back to "unknown" if Redis is unavailable or prefix not cached.
The collector-rpki container populates this cache via write_rpki_cache().
"""

import os
import redis

_r: redis.Redis | None = None
RPKI_CACHE_TTL = 14400  # 4 hours


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
        key = f"rpki:{prefix}:{origin_asn}"
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
        key = f"rpki:{prefix}:{origin_asn}"
        r.set(key, status, ex=RPKI_CACHE_TTL)
    except Exception:
        pass


def warm_rpki_cache_from_db(pool_sync) -> int:
    """
    Warm Redis from TimescaleDB rpki_status table on startup.
    Returns number of entries loaded.
    """
    try:
        r    = _get_redis()
        rows = pool_sync.execute(
            "SELECT prefix, origin_asn, status FROM rpki_status "
            "WHERE time > NOW() - INTERVAL '4 hours'"
        )
        pipe = r.pipeline()
        count = 0
        for row in rows:
            key = f"rpki:{row[0]}:{row[1]}"
            pipe.set(key, row[2], ex=RPKI_CACHE_TTL)
            count += 1
        pipe.execute()
        return count
    except Exception:
        return 0
