"""
api/deps.py
Shared dependency injection — DB pools, ES client, Redis.
All FastAPI routes import from here via Depends().
"""

import asyncpg
import redis.asyncio as aioredis
from elasticsearch import AsyncElasticsearch
import os

TIMESCALE_DSN  = os.getenv("TIMESCALE_DSN",  "postgresql://inetuser:changeme_timescale@timescaledb:5432/inethealth")
REDIS_URL      = os.getenv("REDIS_URL",      "redis://redis:6379/0")
ES_URL         = os.getenv("ES_URL",         "http://elasticsearch:9200")
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://neo4j:7687")
NEO4J_AUTH_STR = os.getenv("NEO4J_AUTH",     "neo4j/changeme_neo4j")

_pg_pool: asyncpg.Pool       | None = None
_redis:   aioredis.Redis     | None = None
_es:      AsyncElasticsearch | None = None


async def get_pg_pool() -> asyncpg.Pool:
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = await asyncpg.create_pool(
            dsn=TIMESCALE_DSN,
            min_size=2,
            max_size=10,
            command_timeout=15,
        )
    return _pg_pool


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def get_es() -> AsyncElasticsearch:
    global _es
    if _es is None:
        # Use basic_auth-free init; ES 8.13 container has security disabled
        _es = AsyncElasticsearch(
            ES_URL,
            verify_certs=False,
            ssl_show_warn=False,
            http_compress=True,
        )
    return _es


async def close_all():
    global _pg_pool, _redis, _es
    if _pg_pool:
        await _pg_pool.close()
    if _redis:
        await _redis.aclose()
    if _es:
        await _es.close()
