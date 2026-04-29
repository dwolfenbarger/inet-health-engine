"""
collectors/db.py
Database connection pool management.
All collectors use these shared pools — never create raw connections.
"""

import asyncpg
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential
from collectors.config import settings

log = structlog.get_logger()

# Module-level pool — initialized once per process
_pg_pool: asyncpg.Pool | None = None


@retry(
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=2, min=2, max=30)
)
async def get_pg_pool() -> asyncpg.Pool:
    """Return the shared asyncpg pool, creating it if needed."""
    global _pg_pool
    if _pg_pool is None:
        log.info("db_connecting", dsn=settings.timescale_dsn.split("@")[-1])
        _pg_pool = await asyncpg.create_pool(
            dsn=settings.timescale_dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        log.info("db_pool_ready")
    return _pg_pool


async def close_pg_pool():
    global _pg_pool
    if _pg_pool:
        await _pg_pool.close()
        _pg_pool = None
        log.info("db_pool_closed")
