"""
collectors/path_engine.py

Phase 3 AS path computation engine.
Uses Neo4j graph to compute and analyze AS-level paths.

Capabilities:
  - Source-to-destination AS path query (shortest + all paths)
  - Path latency annotation from RIPE Atlas RTT data
  - Historical path comparison (what changed vs 24h ago?)
  - Alternative path enumeration (peering vs transit)
  - Path stability scoring

Exposed via FastAPI at /api/v1/bgp/path
"""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import structlog

from collectors.config import settings
from collectors.db import get_pg_pool

log = structlog.get_logger("path_engine")


# ─────────────────────────────────────────────
# Neo4j driver singleton
# ─────────────────────────────────────────────

_neo4j_driver = None


async def get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        try:
            from neo4j import AsyncGraphDatabase
            user, password = settings.neo4j_auth.split("/", 1)
            _neo4j_driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(user, password),
            )
        except Exception as e:
            log.error("neo4j_driver_error", error=str(e))
    return _neo4j_driver


# ─────────────────────────────────────────────
# Path queries
# ─────────────────────────────────────────────

async def find_as_paths(
    source_asn: int,
    dest_asn: int,
    max_hops: int = 10,
    max_paths: int = 5,
) -> list[dict]:
    """
    Find AS-level paths from source_asn to dest_asn in Neo4j graph.
    Returns up to max_paths shortest paths with hop details.
    """
    driver = await get_neo4j_driver()
    if not driver:
        return await _fallback_path_from_timescale(source_asn, dest_asn)

    try:
        async with driver.session() as session:
            result = await session.run("""
                MATCH path = shortestPath(
                    (src:AS {asn: $src})-[*1..$max_hops]-(dst:AS {asn: $dst})
                )
                RETURN path,
                       [n IN nodes(path) | n.asn]     AS asns,
                       [n IN nodes(path) | n.name]    AS names,
                       [n IN nodes(path) | n.tier]    AS tiers,
                       [r IN relationships(path) | type(r)] AS rel_types,
                       length(path)                   AS hops
                LIMIT $max_paths
            """, src=source_asn, dst=dest_asn,
                 max_hops=max_hops, max_paths=max_paths)

            paths = []
            async for record in result:
                paths.append({
                    "asns":      record["asns"],
                    "names":     record["names"],
                    "tiers":     record["tiers"],
                    "rel_types": record["rel_types"],
                    "hops":      record["hops"],
                    "path_type": _classify_path_type(record["rel_types"]),
                })

            if paths:
                log.info("neo4j_paths_found",
                         src=source_asn, dst=dest_asn,
                         count=len(paths))
                return paths

    except Exception as e:
        log.warning("neo4j_path_error", error=str(e))

    # Fallback to TimescaleDB AS path reconstruction
    return await _fallback_path_from_timescale(source_asn, dest_asn)


def _classify_path_type(rel_types: list[str]) -> str:
    """Classify path as peer-to-peer, transit, or mixed."""
    if not rel_types:
        return "unknown"
    has_transit = any("TRANSIT" in r for r in rel_types)
    has_peer    = any("PEERS" in r for r in rel_types)
    if has_transit and not has_peer:
        return "transit"
    if has_peer and not has_transit:
        return "peer"
    return "mixed"


async def _fallback_path_from_timescale(
    source_asn: int,
    dest_asn: int,
) -> list[dict]:
    """
    Fallback: reconstruct paths from TimescaleDB bgp_updates AS paths.
    Finds real observed paths containing both source and dest ASNs.
    """
    try:
        pool = await get_pg_pool()
        rows = await pool.fetch("""
            SELECT as_path, time, prefix
            FROM bgp_updates
            WHERE $1 = ANY(as_path)
              AND $2 = ANY(as_path)
              AND time > NOW() - INTERVAL '24 hours'
            ORDER BY time DESC
            LIMIT 10
        """, source_asn, dest_asn)

        paths = []
        seen = set()
        for r in rows:
            path_key = tuple(r["as_path"])
            if path_key in seen:
                continue
            seen.add(path_key)

            # Trim path to src→dst segment
            asns = list(r["as_path"])
            try:
                src_idx = asns.index(source_asn)
                dst_idx = asns.index(dest_asn)
                segment = asns[min(src_idx, dst_idx):max(src_idx, dst_idx)+1]
            except ValueError:
                segment = asns

            paths.append({
                "asns":      segment,
                "names":     [f"AS{a}" for a in segment],
                "tiers":     [None] * len(segment),
                "rel_types": ["OBSERVED"] * max(len(segment)-1, 0),
                "hops":      len(segment) - 1,
                "path_type": "observed",
                "prefix":    r["prefix"],
                "observed_at": r["time"].isoformat() if r["time"] else None,
                "source":    "timescaledb_fallback",
            })

        return paths

    except Exception as e:
        log.warning("timescale_path_fallback_error", error=str(e))
        return []

async def get_path_history(
    source_asn: int,
    dest_asn: int,
    hours: int = 24,
) -> list[dict]:
    """
    Pull historical AS paths between two ASNs from TimescaleDB.
    Used for path change detection and stability scoring.
    """
    try:
        pool = await get_pg_pool()
        rows = await pool.fetch("""
            SELECT
                time_bucket('30 minutes', time) AS bucket,
                as_path,
                count(*)                        AS observations
            FROM bgp_updates
            WHERE $1 = ANY(as_path)
              AND $2 = ANY(as_path)
              AND time > NOW() - ($3 || ' hours')::INTERVAL
            GROUP BY bucket, as_path
            ORDER BY bucket DESC
        """, source_asn, dest_asn, str(hours))

        return [
            {
                "bucket":       r["bucket"].isoformat(),
                "as_path":      list(r["as_path"]),
                "observations": r["observations"],
            }
            for r in rows
        ]

    except Exception as e:
        log.warning("path_history_error", error=str(e))
        return []


def compute_path_stability(history: list[dict]) -> dict:
    """
    Compute path stability score from historical data.
    Returns score 0-100 (100 = perfectly stable, no changes).
    """
    if not history:
        return {"score": 0, "path_count": 0, "dominant_path": None}

    # Count unique paths
    path_counter: dict[tuple, int] = {}
    for h in history:
        key = tuple(h["as_path"])
        path_counter[key] = path_counter.get(key, 0) + h["observations"]

    total_obs    = sum(path_counter.values())
    unique_paths = len(path_counter)

    if total_obs == 0:
        return {"score": 0, "path_count": 0, "dominant_path": None}

    # Dominant path frequency
    dominant_path = max(path_counter, key=lambda k: path_counter[k])
    dominant_pct  = path_counter[dominant_path] / total_obs

    # Stability = how concentrated observations are on one path
    # Perfect stability = all obs on same path = score 100
    # Many equal paths = low stability
    stability = round(dominant_pct * 100 * (1 - (unique_paths - 1) * 0.05), 1)
    stability = max(0.0, min(stability, 100.0))

    return {
        "score":        stability,
        "path_count":   unique_paths,
        "dominant_path": list(dominant_path),
        "dominant_pct": round(dominant_pct * 100, 1),
        "total_observations": total_obs,
    }


async def annotate_with_latency(paths: list[dict]) -> list[dict]:
    """
    Annotate AS paths with RTT data from RIPE Atlas measurements.
    Pulls from atlas_measurements table if available.
    """
    try:
        pool = await get_pg_pool()

        # Check if atlas_measurements table exists
        exists = await pool.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'atlas_measurements'
            )
        """)
        if not exists:
            return paths

        for path in paths:
            asns = path.get("asns", [])
            if len(asns) < 2:
                continue

            # Try to find RTT data for origin and destination ASNs
            latency_data = []
            for asn in asns:
                rows = await pool.fetch("""
                    SELECT avg_rtt, min_rtt, measurement_type
                    FROM atlas_measurements
                    WHERE probe_asn = $1
                      AND time > NOW() - INTERVAL '1 hour'
                      AND measurement_type IN ('ping', 'traceroute')
                    LIMIT 3
                """, asn)
                if rows:
                    rtts = [r["avg_rtt"] for r in rows if r["avg_rtt"] is not None]
                    if rtts:
                        latency_data.append({
                            "asn":     asn,
                            "avg_rtt": round(sum(rtts) / len(rtts), 2),
                        })

            if latency_data:
                path["latency_data"] = latency_data
                path["total_estimated_rtt"] = round(
                    sum(d["avg_rtt"] for d in latency_data), 2
                )

    except Exception as e:
        log.warning("latency_annotation_error", error=str(e))

    return paths


async def compute_full_path_analysis(
    source_asn: int,
    dest_asn: int,
) -> dict:
    """
    Full path analysis — paths, history, stability, latency.
    This is what the API endpoint calls.
    """
    log.info("path_analysis_start", src=source_asn, dst=dest_asn)

    # Run all queries concurrently
    paths_task   = find_as_paths(source_asn, dest_asn)
    history_task = get_path_history(source_asn, dest_asn)

    paths, history = await asyncio.gather(paths_task, history_task)

    # Annotate paths with latency
    paths = await annotate_with_latency(paths)

    # Compute stability
    stability = compute_path_stability(history)

    result = {
        "source_asn":  source_asn,
        "dest_asn":    dest_asn,
        "path_count":  len(paths),
        "paths":       paths,
        "stability":   stability,
        "history_buckets": len(history),
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    log.info("path_analysis_complete",
             src=source_asn, dst=dest_asn,
             paths=len(paths),
             stability=stability.get("score"))

    return result
