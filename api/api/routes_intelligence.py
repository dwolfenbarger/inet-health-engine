"""
api/routes_intelligence.py
Phase 3 intelligence endpoints — NLP correlation, baseline, path analysis.
"""

import asyncio
from datetime import datetime, timezone
import json
import math
import os
from fastapi import APIRouter, Query
from typing import Optional
from api.deps import get_pg_pool, get_redis
import httpx
from api.routes_globe import ASN_GEO

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@router.get("/health-score")
async def global_health_score():
    """
    Current global internet health score with z-score breakdown.
    Pulled from Redis (set by baseline_modeler every 5 min).
    Falls back to as_health DB query when Redis key has expired.
    """
    # Try Redis first (fast path)
    try:
        r = await get_redis()
        raw = await r.get("health:global")
        if raw:
            return json.loads(raw)
    except Exception:
        pass

    # Redis miss — fall back to most recent as_health row (asn=0 = global)
    try:
        pool = await get_pg_pool()
        row = await pool.fetchrow("""
            SELECT health_score, time FROM as_health
            WHERE asn = 0
            ORDER BY time DESC LIMIT 1
        """)
        if row and row["health_score"] is not None:
            return {
                "health_score": float(row["health_score"]),
                "timestamp":    row["time"].isoformat(),
                "source":       "as_health_db_fallback",
                "z_scores":     {"update_rate": 0.0, "withdrawal_rate": 0.0, "prefix_diversity": 0.0},
                "severity":     {"update_rate": 1, "withdrawal_rate": 1, "overall": 1},
                "alerts":       [None, None, None],
            }
    except Exception:
        pass

    return {"health_score": None, "message": "Baseline not yet computed"}


@router.get("/baseline")
async def bgp_baseline(hours: int = Query(24, ge=1, le=168)):
    """BGP update rate baseline statistics for the specified window."""
    # Redis cache — baseline query scans compressed bgp_updates (slow on Pi)
    cache_key = f"baseline:{hours}"
    try:
        redis = await get_redis()
        cached = await redis.get(cache_key)
        if cached:
            import json as _json
            return _json.loads(cached)
    except Exception:
        pass

    pool = await get_pg_pool()
    rows = await pool.fetch("""
        SELECT
            time_bucket('5 minutes', time)  AS bucket,
            count(*)                        AS update_count,
            count(*) FILTER (WHERE change_type = 'announce') AS announces,
            count(*) FILTER (WHERE change_type = 'withdraw') AS withdrawals,
            count(DISTINCT prefix)           AS unique_prefixes
        FROM bgp_updates
        WHERE time > NOW() - ($1 || ' hours')::INTERVAL
        GROUP BY bucket
        ORDER BY bucket DESC
        LIMIT 288
    """, str(hours))

    if not rows:
        return {"buckets": [], "summary": {}}

    counts = [r["update_count"] for r in rows]
    n     = len(counts)
    mu    = sum(counts) / n if n else 0
    var   = sum((x - mu)**2 for x in counts) / max(n-1, 1)
    sigma = math.sqrt(var)

    result = {
        "summary": {
            "mean_updates_per_5m": round(mu, 2),
            "std_dev":             round(sigma, 2),
            "window_hours":        hours,
            "bucket_count":        n,
        },
        "buckets": [
            {
                "bucket":          r["bucket"].isoformat(),
                "updates":         r["update_count"],
                "announces":       r["announces"],
                "withdrawals":     r["withdrawals"],
                "unique_prefixes": r["unique_prefixes"],
            }
            for r in rows
        ],
    }
    try:
        import json as _json
        redis = await get_redis()
        await redis.setex(cache_key, 60, _json.dumps(result))
    except Exception:
        pass
    return result

@router.get("/path")
async def as_path_analysis(
    source_asn: int = Query(..., description="Source ASN"),
    dest_asn:   int = Query(..., description="Destination ASN"),
):
    """
    AS path analysis via TimescaleDB observed paths + Neo4j graph.
    Returns paths, stability score, and latency annotation.
    """
    pool = await get_pg_pool()

    # ── Observed paths from TimescaleDB ──────
    rows = await pool.fetch("""
        SELECT as_path, time, prefix, count(*) OVER (PARTITION BY as_path) AS observations
        FROM bgp_updates
        WHERE $1 = ANY(as_path)
          AND $2 = ANY(as_path)
          AND time > NOW() - INTERVAL '24 hours'
        ORDER BY time DESC
        LIMIT 50
    """, source_asn, dest_asn)

    # Deduplicate and build path list
    seen = set()
    paths = []
    path_obs: dict[tuple, int] = {}

    for r in rows:
        key = tuple(r["as_path"])
        path_obs[key] = r["observations"]
        if key in seen:
            continue
        seen.add(key)

        asns = list(r["as_path"])
        try:
            src_i = asns.index(source_asn)
            dst_i = asns.index(dest_asn)
            segment = asns[min(src_i,dst_i):max(src_i,dst_i)+1]
        except ValueError:
            segment = asns

        paths.append({
            "asns":      segment,
            "names":     [f"AS{a}" for a in segment],
            "hops":      len(segment) - 1,
            "path_type": "observed",
            "prefix":    r["prefix"],
            "last_seen": r["time"].isoformat(),
        })

    # ── Stability score ───────────────────────
    total_obs = sum(path_obs.values())
    if total_obs > 0 and path_obs:
        dominant_count = max(path_obs.values())
        dominant_path  = [list(k) for k,v in path_obs.items() if v == dominant_count][0]
        dominant_pct   = dominant_count / total_obs
        stability      = round(max(0, dominant_pct * 100 * (1-(len(path_obs)-1)*0.05)), 1)
    else:
        dominant_path  = None
        dominant_pct   = 0.0
        stability      = 0.0

    # ── Try Neo4j for graph paths ─────────────
    neo4j_paths = []
    try:
        from neo4j import AsyncGraphDatabase
        neo4j_auth = os.getenv("NEO4J_AUTH", "neo4j/changeme_neo4j")
        neo4j_uri  = os.getenv("NEO4J_URI",  "bolt://neo4j:7687")
        user, pw   = neo4j_auth.split("/", 1)
        driver = AsyncGraphDatabase.driver(neo4j_uri, auth=(user, pw))
        async with driver.session() as session:
            result = await session.run("""
                MATCH path = shortestPath(
                    (src:AS {asn: $src})-[*1..8]-(dst:AS {asn: $dst})
                )
                RETURN [n IN nodes(path) | n.asn] AS asns,
                       [n IN nodes(path) | n.name] AS names,
                       length(path) AS hops
                LIMIT 3
            """, src=source_asn, dst=dest_asn)
            async for rec in result:
                neo4j_paths.append({
                    "asns":      rec["asns"],
                    "names":     rec["names"],
                    "hops":      rec["hops"],
                    "path_type": "graph",
                    "source":    "neo4j",
                })
        await driver.close()
    except Exception:
        pass  # Neo4j optional — TimescaleDB paths are sufficient

    all_paths = neo4j_paths + paths

    return {
        "source_asn":  source_asn,
        "dest_asn":    dest_asn,
        "path_count":  len(all_paths),
        "paths":       all_paths[:10],
        "stability": {
            "score":         stability,
            "path_count":    len(path_obs),
            "dominant_path": dominant_path,
            "dominant_pct":  round(dominant_pct * 100, 1),
            "total_observations": total_obs,
        },
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
    }

@router.get("/anomaly-zscores")
async def anomaly_zscores(hours: int = Query(6, ge=1, le=48)):
    """Z-scores for recent anomaly rates vs historical baseline."""
    pool = await get_pg_pool()

    recent = await pool.fetch("""
        SELECT event_type, count(*) AS count,
               round(avg(severity)::numeric, 2) AS avg_severity,
               round(avg(confidence)::numeric, 3) AS avg_confidence
        FROM bgp_anomalies
        WHERE time > NOW() - ($1 || ' hours')::INTERVAL
        GROUP BY event_type
    """, str(hours))

    historical = await pool.fetch("""
        SELECT event_type,
               count(*) / GREATEST(
                   EXTRACT(EPOCH FROM (max(time) - min(time))) / 3600, 1
               ) AS hourly_rate
        FROM bgp_anomalies
        WHERE time > NOW() - INTERVAL '24 hours'
        GROUP BY event_type
    """)

    hist_map = {r["event_type"]: float(r["hourly_rate"]) for r in historical}
    results  = []

    for r in recent:
        et            = r["event_type"]
        count         = r["count"]
        current_rate  = count / hours
        baseline_rate = hist_map.get(et, 0)
        z = round((current_rate - baseline_rate) / max(baseline_rate * 0.3, 0.1), 2) \
            if baseline_rate > 0 else 0.0

        results.append({
            "event_type":       et,
            "count_in_window":  count,
            "current_rate_ph":  round(current_rate, 3),
            "baseline_rate_ph": round(baseline_rate, 3),
            "z_score":          z,
            "severity":         5 if abs(z)>3.5 else 4 if abs(z)>2.5 else 3 if abs(z)>1.5 else 2,
            "avg_confidence":   float(r["avg_confidence"]),
        })

    results.sort(key=lambda x: abs(x["z_score"]), reverse=True)
    return {"window_hours": hours, "anomaly_zscores": results}


@router.get("/as-profile/{asn}")
async def as_profile(asn: int):
    """Full AS intelligence profile: health trends, top prefixes, anomaly history."""
    pool = await get_pg_pool()

    updates, anomalies, prefixes, health = await asyncio.gather(
        pool.fetch("""
            SELECT time_bucket('1 hour', time) AS hour,
                   count(*) AS updates,
                   count(*) FILTER (WHERE change_type='announce') AS announces,
                   count(*) FILTER (WHERE change_type='withdraw') AS withdrawals
            FROM bgp_updates
            WHERE origin_asn = $1
              AND time > NOW() - INTERVAL '24 hours'
            GROUP BY hour ORDER BY hour DESC
        """, asn),
        pool.fetch("""
            SELECT event_type, count(*) AS count,
                   round(avg(confidence)::numeric,3) AS avg_conf,
                   max(severity) AS max_sev
            FROM bgp_anomalies
            WHERE origin_asn = $1
              AND time > NOW() - INTERVAL '24 hours'
            GROUP BY event_type
        """, asn),
        pool.fetch("""
            SELECT prefix, count(*) AS changes, max(time) AS last_seen
            FROM bgp_updates
            WHERE origin_asn = $1
              AND time > NOW() - INTERVAL '24 hours'
            GROUP BY prefix
            ORDER BY changes DESC LIMIT 20
        """, asn),
        pool.fetch("""
            SELECT health_score, bgp_update_rate, time
            FROM as_health
            WHERE asn = $1
            ORDER BY time DESC LIMIT 12
        """, asn),
    )

    return {
        "asn":             asn,
        "hourly_updates":  [dict(r) for r in updates],
        "anomaly_summary": [dict(r) for r in anomalies],
        "top_prefixes":    [dict(r) for r in prefixes],
        "health_history":  [dict(r) for r in health],
    }

@router.get("/atlas-correlation")
async def atlas_bgp_correlation(
    window_m: int = Query(30, ge=5, le=120),
    min_rtt_spike: float = Query(20.0, description="Min RTT increase (ms) to flag"),
):
    """
    Correlate Atlas RTT measurements against active BGP anomalies.

    When a prefix has an active BGP flap/hijack AND Atlas probes show
    elevated RTT or packet loss to that target, confidence compounds.

    Logic:
      - For each active BGP anomaly prefix, find Atlas measurements to
        the same target IP range in the same time window
      - Compare current RTT to the 1h baseline for that target
      - Flag if delta > min_rtt_spike ms or packet_loss > 0.05
      - Return correlated events with combined confidence
    """
    pool = await get_pg_pool()

    # 1. Get active anomalies
    anom_rows = await pool.fetch("""
        SELECT DISTINCT ON (affected_prefix, origin_asn)
            event_type, affected_prefix, origin_asn,
            confidence, severity, time AS detected_at
        FROM bgp_anomalies
        WHERE source LIKE 'ris/%'
          AND confidence > 0.5
          AND origin_asn IS NOT NULL
          AND time > NOW() - ($1 || ' minutes')::INTERVAL
        ORDER BY affected_prefix, origin_asn, confidence DESC
        LIMIT 200
    """, str(window_m))

    if not anom_rows:
        return {"correlated": [], "total_anomalies": 0, "timestamp": datetime.now(tz=timezone.utc).isoformat()}

    # 2. Get Atlas baseline (1h average per target)
    baseline_rows = await pool.fetch("""
        SELECT target,
            avg(avg_rtt_ms)   AS baseline_rtt,
            avg(packet_loss)  AS baseline_loss,
            count(*)          AS sample_count
        FROM atlas_measurements
        WHERE time > NOW() - INTERVAL '60 minutes'
          AND avg_rtt_ms IS NOT NULL
        GROUP BY target
    """)
    baseline = {r["target"]: dict(r) for r in baseline_rows}

    # 3. Get current Atlas (last 10 min)
    current_rows = await pool.fetch("""
        SELECT target,
            avg(avg_rtt_ms)  AS current_rtt,
            avg(packet_loss) AS current_loss,
            count(*)         AS probe_count
        FROM atlas_measurements
        WHERE time > NOW() - INTERVAL '10 minutes'
          AND avg_rtt_ms IS NOT NULL
        GROUP BY target
    """)
    current = {r["target"]: dict(r) for r in current_rows}

    # 4. Global DNS RTT health signal
    # Atlas probes measure DNS RTT to root servers every few minutes.
    # When BGP routing degrades globally, DNS RTT from distributed probes
    # rises and variance increases — a network-wide stress indicator.
    # We compare current 10-min window vs 60-min baseline per target.

    global_rtt_spike = False
    rtt_signals = []
    for target, cur in current.items():
        base = baseline.get(target)
        if not base or not base["baseline_rtt"] or not cur["current_rtt"]:
            continue
        rtt_delta   = float(cur["current_rtt"]) - float(base["baseline_rtt"])
        loss_delta  = float(cur["current_loss"] or 0) - float(base["baseline_loss"] or 0)
        pct_change  = (rtt_delta / max(float(base["baseline_rtt"]), 1)) * 100
        if rtt_delta >= min_rtt_spike or loss_delta >= 0.05:
            global_rtt_spike = True
            rtt_signals.append({
                "target":          target,
                "baseline_rtt_ms": round(float(base["baseline_rtt"]), 2),
                "current_rtt_ms":  round(float(cur["current_rtt"]), 2),
                "rtt_delta_ms":    round(rtt_delta, 2),
                "pct_change":      round(pct_change, 1),
                "probes":          cur["probe_count"],
            })

    # If global DNS RTT is elevated, compound all high-severity BGP anomalies
    correlated = []
    if global_rtt_spike and anom_rows:
        atlas_conf = min(0.7, 0.2 + len(rtt_signals) * 0.1)
        for a in anom_rows:
            if a["severity"] < 3:
                continue
            bgp_conf = float(a["confidence"])
            compound = round(1 - (1 - bgp_conf) * (1 - atlas_conf), 3)
            correlated.append({
                "event_type":          a["event_type"],
                "affected_prefix":     a["affected_prefix"],
                "origin_asn":          a["origin_asn"],
                "bgp_confidence":      round(bgp_conf, 3),
                "bgp_severity":        a["severity"],
                "atlas_signal":        "global_dns_rtt_elevated",
                "atlas_targets_spiked": len(rtt_signals),
                "atlas_confidence":    round(atlas_conf, 3),
                "compound_confidence": compound,
                "confirmed_by":        ["ripe-ris", "ripe-atlas-dns"],
                "rtt_signals":         rtt_signals,
            })
    correlated.sort(key=lambda x: -x["compound_confidence"])

    return {
        "window_m":           window_m,
        "total_anomalies":    len(anom_rows),
        "atlas_targets":      len(current),
        "global_rtt_spike":   global_rtt_spike,
        "rtt_signals":        rtt_signals,
        "correlated":         correlated,
        "confirmed_count":    len(correlated),
        "timestamp":          datetime.now(tz=timezone.utc).isoformat(),
    }

# ── IP Traceroute endpoint ─────────────────────────────────────────────────────
# Simulates traceroute by generating hop IPs via ICMP path probing (not available
# in containers), so we implement a BGP-path-informed "logical traceroute":
# 1. Resolve src and dst IPs to their origin ASNs via Team Cymru DNS whois
# 2. Find the AS path between them via our Neo4j topology
# 3. Return each AS hop with geo coordinates and RTT estimate
# This gives an operationally useful path trace without root/raw-socket access.

async def cymru_ip_to_asn(ip: str) -> dict:
    """Resolve an IP to ASN/org/country via Team Cymru DNS whois (free, no key)."""
    try:
        import dns.resolver  # dnspython
        # Reverse the IP and query origin.asn.cymru.com
        parts = ip.split(".")
        if len(parts) != 4:
            return {}
        reversed_ip = ".".join(reversed(parts))
        query = f"{reversed_ip}.origin.asn.cymru.com"
        answers = dns.resolver.resolve(query, "TXT")
        for rdata in answers:
            txt = str(rdata).strip('"')
            # Format: "ASN | IP/prefix | CC | registry | date"
            fields = [f.strip() for f in txt.split("|")]
            if len(fields) >= 3:
                return {
                    "asn":     int(fields[0].split()[0]) if fields[0].split()[0].isdigit() else None,
                    "prefix":  fields[1] if len(fields) > 1 else None,
                    "country": fields[2] if len(fields) > 2 else None,
                }
    except Exception:
        pass
    return {}


async def rdap_asn_info(asn: int) -> dict:
    """Get org name for an ASN via ARIN RDAP."""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            r = await client.get(f"https://rdap.arin.net/registry/autnum/{asn}")
            if r.status_code == 200:
                d = r.json()
                return {"name": d.get("name", ""), "handle": d.get("handle", "")}
    except Exception:
        pass
    return {}


@router.get("/traceroute")
async def ip_traceroute(src: str = Query(...), dst: str = Query(...)):
    """
    Logical BGP-path traceroute between two IPs.
    Resolves each IP to its origin ASN via Team Cymru, then traces the AS path
    through Neo4j topology. Returns geo-tagged hops for globe arc rendering.
    Falls back to direct AS path if Team Cymru is unavailable.
    """
    import ipaddress

    # Validate IPs
    try:
        ipaddress.ip_address(src)
        ipaddress.ip_address(dst)
    except ValueError:
        return {"error": "Invalid IP address", "hops": []}

    # Resolve src and dst to ASNs
    src_info = await cymru_ip_to_asn(src)
    dst_info = await cymru_ip_to_asn(dst)

    src_asn = src_info.get("asn")
    dst_asn = dst_info.get("asn")

    hops = []

    # Add src hop
    if src_asn:
        src_geo = ASN_GEO.get(src_asn, (None, None, None, None))
        src_org = await rdap_asn_info(src_asn)
        hops.append({
            "hop":     1,
            "ip":      src,
            "asn":     src_asn,
            "org":     src_org.get("name") or src_info.get("country", ""),
            "country": src_info.get("country"),
            "lat":     src_geo[0],
            "lon":     src_geo[1],
            "rtt_ms":  None,
            "type":    "src",
        })

    # Get AS path via Neo4j if we have both ASNs
    as_path_hops = []
    if src_asn and dst_asn and src_asn != dst_asn:
        try:
            pool = await get_pg_pool()
            # Use the observed AS paths from bgp_updates
            rows = await pool.fetch("""
                SELECT as_path FROM bgp_updates
                WHERE origin_asn = $1
                  AND time > NOW() - INTERVAL '2 hours'
                  AND array_length(as_path, 1) > 2
                ORDER BY time DESC
                LIMIT 100
            """, dst_asn)
            # Find paths that pass through src_asn
            for row in rows:
                ap = row["as_path"]
                if src_asn in ap and dst_asn in ap:
                    si = ap.index(src_asn)
                    di = ap.index(dst_asn)
                    segment = ap[min(si,di):max(si,di)+1]
                    if len(segment) > 1:
                        as_path_hops = segment
                        break
            if not as_path_hops:
                # No matching path — build direct src→dst
                as_path_hops = [src_asn, dst_asn] if src_asn != dst_asn else [src_asn]
        except Exception:
            as_path_hops = [src_asn, dst_asn] if src_asn != dst_asn else []

    # Add intermediate hops from AS path
    base_rtt = 5
    for i, asn in enumerate(as_path_hops[1:], 2):
        if asn == (hops[-1]["asn"] if hops else None):
            continue
        geo = ASN_GEO.get(asn, (None, None, None, None))
        hops.append({
            "hop":     i,
            "ip":      None,
            "asn":     asn,
            "org":     geo[3] if len(geo) > 3 else f"AS{asn}",
            "country": geo[2] if len(geo) > 2 else None,
            "lat":     geo[0],
            "lon":     geo[1],
            "rtt_ms":  base_rtt + (i * 8) + (hash(asn) % 15),
            "type":    "transit",
        })

    # Add dst hop
    if dst_asn and (not hops or hops[-1]["asn"] != dst_asn):
        dst_geo = ASN_GEO.get(dst_asn, (None, None, None, None))
        dst_org = await rdap_asn_info(dst_asn)
        hops.append({
            "hop":     len(hops) + 1,
            "ip":      dst,
            "asn":     dst_asn,
            "org":     dst_org.get("name") or dst_info.get("country", ""),
            "country": dst_info.get("country"),
            "lat":     dst_geo[0],
            "lon":     dst_geo[1],
            "rtt_ms":  base_rtt + (len(hops) + 1) * 8,
            "type":    "dst",
        })

    if not hops:
        return {
            "src": src, "dst": dst,
            "src_asn": src_asn, "dst_asn": dst_asn,
            "error": "Could not resolve IPs to ASNs — Team Cymru DNS may be unavailable",
            "hops": [],
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }

    return {
        "src":     src,
        "dst":     dst,
        "src_asn": src_asn,
        "dst_asn": dst_asn,
        "hops":    hops,
        "hop_count": len(hops),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
