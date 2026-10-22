"""Origin-change hijack detector."""
import asyncio
from datetime import datetime, timezone, timedelta
import structlog
from collectors.db import get_pg_pool

log = structlog.get_logger("hijack_detector")


async def detect_origin_changes(pool, window_minutes: int = 30):
    """Flag recent origins that differ from the established baseline."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH recent AS (
                SELECT DISTINCT prefix, origin_asn, time
                FROM bgp_updates
                WHERE time > $1 AND origin_asn IS NOT NULL
            ),
            baseline AS (
                SELECT prefix, origin_asn, last_seen, observation_count
                FROM prefix_origins
                WHERE observation_count >= 5
            )
            SELECT r.prefix, r.origin_asn AS new_origin, b.origin_asn AS expected_origin,
                   r.time, b.observation_count
            FROM recent r
            JOIN baseline b ON r.prefix = b.prefix
            WHERE r.origin_asn != b.origin_asn
              AND NOT EXISTS (
                  SELECT 1 FROM prefix_origins po
                  WHERE po.prefix = r.prefix AND po.origin_asn = r.origin_asn
                    AND po.observation_count >= 5
              )
            LIMIT 200
            """,
            cutoff,
        )
        inserted = 0
        for row in rows:
            await conn.execute(
                """
                INSERT INTO bgp_anomalies
                  (time, event_type, affected_prefix, origin_asn, expected_asn,
                   severity, confidence, source, raw_data)
                VALUES ($1, 'hijack_origin_change', $2, $3, $4, 4, 0.8, 'detector',
                        jsonb_build_object('baseline_count', $5))
                """,
                row["time"],
                row["prefix"],
                row["new_origin"],
                row["expected_origin"],
                row["observation_count"],
            )
            inserted += 1
        return inserted


async def run_once():
    pool = await get_pg_pool()
    n = await detect_origin_changes(pool)
    log.info("hijack_scan_complete", inserted=n)
    return n


if __name__ == "__main__":
    asyncio.run(run_once())
