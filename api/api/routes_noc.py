"""Bounded, read-only RIS observations for the global NOC interface.

These are update observations, not a complete RIB or a performance measurement.
All SQL arguments are bound. No synthetic collector is eligible.
"""
from datetime import datetime, timezone
from ipaddress import ip_network
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from api.deps import get_pg_pool

router = APIRouter(prefix="/noc", tags=["noc"])
COLLECTORS = {
    "rrc00": {"city": "Amsterdam", "lat": 52.37, "lon": 4.90},
    "rrc11": {"city": "New York", "lat": 40.71, "lon": -74.01},
    "rrc14": {"city": "Palo Alto", "lat": 37.44, "lon": -122.14},
    "rrc16": {"city": "Miami", "lat": 25.76, "lon": -80.19},
    "rrc21": {"city": "Paris", "lat": 48.86, "lon": 2.35},
}
CAP = 20000


def freshness(last_seen, now):
    if last_seen is None:
        return {"state": "unavailable", "age_seconds": None}
    age = max(0, (now - last_seen).total_seconds())
    return {"state": "fresh" if age <= 240 else "stale", "age_seconds": round(age)}


def canonical_prefix(value):
    if not value:
        return None
    try:
        return str(ip_network(value, strict=False))
    except ValueError:
        raise HTTPException(422, "Enter an IPv4 or IPv6 CIDR prefix")


@router.get("/snapshot")
async def snapshot(
    minutes: int = Query(15, ge=1, le=60),
    family: Literal["all", "4", "6"] = "all",
    collector: str = Query("all", max_length=10),
    asn: int | None = Query(None, ge=1, le=4294967295),
    prefix: str | None = Query(None, max_length=49),
):
    prefix = canonical_prefix(prefix)
    if collector != "all" and collector not in COLLECTORS:
        raise HTTPException(422, "Unknown configured collector")
    now = datetime.now(timezone.utc)
    try:
        pool = await get_pg_pool()
        # Origin index remains usable for normal allocated ASN ranges. The
        # existing integer schema cannot contain ASNs above signed int32.
        stored_asn = asn if asn is None or asn <= 2147483647 else -1
        rows = await pool.fetch("""
            SELECT time, prefix, origin_asn, as_path, communities,
                   change_type, collector, peer_asn, host(next_hop) AS next_hop, next_hop_raw, rpki_status
            FROM bgp_updates
            WHERE time > NOW() - make_interval(mins => $1)
              AND collector = ANY($2::text[])
              AND ($3::integer IS NULL OR origin_asn = $3)
              AND ($4::text IS NULL OR prefix = $4)
              AND ($5 = 'all' OR ($5 = '6') = (position(':' in prefix) > 0))
              AND ($6 = 'all' OR collector = $6)
            ORDER BY time DESC LIMIT 20001
        """, minutes, list(COLLECTORS), stored_asn, prefix, family, collector)
        capped = len(rows) > CAP
        rows = rows[:CAP]
        # Global source state is independent of the selected resource. An
        # unobserved prefix must not make a working source look unavailable.
        latest = await pool.fetchval("""
            SELECT time FROM bgp_updates
            WHERE time > NOW() - INTERVAL '24 hours'
              AND collector = ANY($1::text[])
            ORDER BY time DESC LIMIT 1
        """, list(COLLECTORS))
        signals = await pool.fetch("""
            SELECT time, event_id::text, event_type, affected_prefix, origin_asn,
                   expected_asn, severity, confidence, source
            FROM bgp_anomalies
            WHERE time > NOW() - make_interval(mins => $1)
              AND source LIKE 'ris/%'
              AND ($2::integer IS NULL OR origin_asn = $2)
              AND ($3::text IS NULL OR affected_prefix = $3)
              AND ($4 = 'all' OR ($4 = '6') = (position(':' in affected_prefix) > 0))
              AND ($5 = 'all' OR source = $6)
            ORDER BY time DESC LIMIT 100
        """, minutes, stored_asn, prefix, family, collector, f"ris/{collector}")
    except Exception as exc:
        # Do not expose DB connection details or silently return a healthy empty result.
        import structlog
        structlog.get_logger("noc").error("snapshot_unavailable", error_type=type(exc).__name__)
        raise HTTPException(503, "Live observation store unavailable; retry shortly") from None

    seen, routes, collectors = set(), [], {}
    for row in rows:
        r = dict(row)
        c = collectors.setdefault(r["collector"], {"updates": 0, "last_seen": r["time"], "peers": set()})
        c["updates"] += 1
        if r["peer_asn"] is not None:
            c["peers"].add(r["peer_asn"])
        key = (r["collector"], r["peer_asn"], r["prefix"])
        if key not in seen and len(routes) < 200:
            seen.add(key)
            # Old enum serialization remains readable; new writes use values.
            r["change_type"] = (r["change_type"] or "unknown").split(".")[-1].lower()
            r["rpki_status"] = (r["rpki_status"] or "unknown").split(".")[-1].lower()
            routes.append(r)
    origins = {}
    for row in rows:
        a = row["origin_asn"]
        if a:
            origins[a] = origins.get(a, 0) + 1
    return {
        "as_of": now, "source": "RIPE RIS Live", "source_state": freshness(latest, now),
        "last_observation": latest, "health_score": None,
        "scope": {"minutes": minutes, "family": family, "collector": collector, "asn": asn, "prefix": prefix,
                  "sample_limit": CAP, "sample_capped": capped, "route_limit": 200},
        "summary": {"updates": len(rows), "origins": len(origins),
                    "prefixes": len({r["prefix"] for r in rows}),
                    "observers": len({(r["collector"], r["peer_asn"]) for r in rows})},
        "collectors": [{"id": name, **geo, "updates": collectors.get(name, {}).get("updates", 0),
                        "last_seen": collectors.get(name, {}).get("last_seen"),
                        "peers": len(collectors.get(name, {}).get("peers", set()))}
                       for name, geo in COLLECTORS.items()],
        "origins": [{"asn": a, "updates": n} for a, n in sorted(origins.items(), key=lambda x: -x[1])[:12]],
        "routes": routes, "signals": [dict(r) for r in signals],
        "limitations": ["Sampled update stream, not a complete routing table or proof of reachability.",
                        "Collector coordinates are approximate city locations, not ASN locations or traffic paths.",
                        "Anomaly candidates are heuristic signals, not confirmed hijacks or outages.",
                        "Congestion, packet loss and latency are not established by this BGP feed.",
                        "Historical AS paths before this release may have lost repeated ASNs.",
                        "Historical candidates without collector attribution are excluded from collector-filtered results.",
                        "Collection is a bounded sample: up to 50,000 updates per 60-second window, scheduled every 90 seconds.",
                        "The next_hop_raw field preserves multi-address IPv6 attributes; next_hop is null when not a single address.",
                        "Complex AS_SET paths cannot be represented by the current schema and have an empty AS sequence."],
        "metadata_source": "https://ris.ripe.net/docs/route-collectors/",
    }
