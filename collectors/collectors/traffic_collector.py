"""
collectors/traffic_collector.py

Traffic and internet health collector.
Sources:
  - Cloudflare Radar API  — traffic trends, BGP signals, attack data
  - CAIDA IODA API        — outage detection (BGP + darknet + DNS signals)
  - PeeringDB API         — AS and IXP topology (slower cadence)

Responsibilities:
  - Poll each source every POLL_INTERVAL seconds
  - Normalize to TrafficMetric and NetworkEvent models
  - Write metrics to TimescaleDB (traffic_metrics table)
  - Write detected outages to TimescaleDB (network_events table)
  - Publish high-severity events to Redis stream (raw.traffic)

Run as:
    python -m collectors.traffic_collector
"""

import asyncio
import json
import signal
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from collectors.config import settings
from collectors.db import get_pg_pool

log = structlog.get_logger("traffic_collector")

# ─────────────────────────────────────────────
# HTTP client — shared across all API calls
# ─────────────────────────────────────────────

def _make_client(base_url: str, headers: dict = None, timeout: int = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers or {},
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
    )

# ─────────────────────────────────────────────
# Cloudflare Radar collector
# Docs: https://developers.cloudflare.com/radar/
# ─────────────────────────────────────────────

RADAR_BASE = "https://api.cloudflare.com/client/v4/radar"

RADAR_REGIONS = {
    "WNAM": "NA",   # Western North America
    "ENAM": "NA",   # Eastern North America
    "WEUR": "EU",   # Western Europe
    "EEUR": "EU",   # Eastern Europe
    "APAC": "APAC",
    "MENA": "MEA",
    "SAM":  "LATAM",
    "OC":   "APAC",
    "AFR":  "MEA",
}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPError),
)
async def fetch_radar_traffic_summary(client: httpx.AsyncClient) -> list[dict]:
    """
    Fetch global HTTP traffic summary by region from Cloudflare Radar.
    Returns normalized traffic_metrics rows.
    """
    if not settings.cloudflare_radar_token:
        log.warning("radar_token_missing", msg="Skipping Cloudflare Radar — no token configured")
        return []

    rows = []
    try:
        resp = await client.get(
            "/http/summary/device_type",
            params={
                "format":    "json",
                "dateRange": "1d",   # required by Radar HTTP summary endpoints; omitting returns 400
            }
        )
        resp.raise_for_status()
        data = resp.json()
        now = datetime.now(tz=timezone.utc)

        # Extract top-level traffic distribution
        summary = data.get("result", {}).get("summary_0", {})
        total = sum(float(v) for v in summary.values()) if summary else 0

        if total > 0:
            rows.append({
                "time":        now,
                "region":      "GLOBAL",
                "country_code": None,
                "asn":         None,
                "metric_type": "traffic_share_desktop",
                "value":       float(summary.get("desktop", 0)),
                "source":      "cloudflare-radar",
            })
            rows.append({
                "time":        now,
                "region":      "GLOBAL",
                "country_code": None,
                "asn":         None,
                "metric_type": "traffic_share_mobile",
                "value":       float(summary.get("mobile", 0)),
                "source":      "cloudflare-radar",
            })

        log.info("radar_traffic_summary_fetched", rows=len(rows))
    except httpx.HTTPStatusError as e:
        log.warning("radar_traffic_summary_error", status=e.response.status_code, error=str(e))
    except Exception as e:
        log.error("radar_traffic_summary_exception", error=str(e))

    return rows


async def fetch_radar_traffic_by_location(client: httpx.AsyncClient) -> list[dict]:
    """
    Fetch per-location (country) HTTP traffic distribution from Cloudflare Radar.
    Populates traffic_metrics with country-level traffic share data.
    Required param: dateRange=1d (Radar rejects requests without a time range).
    """
    if not settings.cloudflare_radar_token:
        return []

    rows = []
    try:
        resp = await client.get(
            "/http/summary/device_type",
            params={"format": "json", "dateRange": "1d"},
        )
        resp.raise_for_status()
        data = resp.json()
        now = datetime.now(tz=timezone.utc)

        # Also fetch BGP route stats as a health signal
        bgp_resp = await client.get(
            "/bgp/routes/stats",
            params={"format": "json"},
        )
        if bgp_resp.status_code == 200:
            bgp_data = bgp_resp.json().get("result", {}).get("stats", {})
            # Radar /bgp/routes/stats field mapping (verified 2026-04-28)
            for metric, key in [
                ("bgp_prefixes_v4",        "distinct_prefixes_ipv4"),
                ("bgp_prefixes_v6",        "distinct_prefixes_ipv6"),
                ("bgp_origins_v4",         "distinct_origins_ipv4"),
                ("bgp_origins_v6",         "distinct_origins_ipv6"),
                ("bgp_routes_total",       "routes_total"),
                ("bgp_routes_valid",       "routes_valid"),
                ("bgp_routes_invalid",     "routes_invalid"),
                ("bgp_routes_unknown",     "routes_unknown"),
                ("bgp_rpki_valid_ratio",   "rpki_valid_ratio"),
            ]:
                val = bgp_data.get(key)
                if val is not None:
                    try:
                        rows.append({
                            "time":         now,
                            "region":       "GLOBAL",
                            "country_code": None,
                            "asn":          None,
                            "metric_type":  metric,
                            "value":        float(val),
                            "source":       "cloudflare-radar",
                        })
                    except (ValueError, TypeError):
                        pass  # ipv6 total_count is an astronomically large string

        log.info("radar_traffic_location_fetched", rows=len(rows))
    except httpx.HTTPStatusError as e:
        log.warning("radar_traffic_location_error", status=e.response.status_code)
    except Exception as e:
        log.warning("radar_traffic_location_exception", error=str(e))

    return rows

async def fetch_radar_bgp_hijacks(client: httpx.AsyncClient) -> list[dict]:
    """
    Fetch BGP hijack events detected by Cloudflare Radar.
    Returns network_event dicts for high-severity routing anomalies.
    """
    if not settings.cloudflare_radar_token:
        return []

    events = []
    try:
        resp = await client.get(
            "/bgp/hijacks/events",
            params={
                "format":      "json",
                "limit": 500,
            }
        )
        resp.raise_for_status()
        data = resp.json()
        now  = datetime.now(tz=timezone.utc)

        for item in data.get("result", {}).get("events", []):
            # Radar API returns confidence_score (0-100), not hijack_score
            # Also derive severity from tags when confidence_score is low
            conf_score = item.get("confidence_score") or item.get("hijack_score") or 0
            tags = {t["name"]: t["score"] for t in item.get("tags", [])}
            # Tag-derived severity boost: RPKI/IRR invalidity raises confidence
            tag_boost = 0.0
            if tags.get("rpki_new_origin_invalid", 0) > 0:
                tag_boost += 0.25
            if tags.get("irr_new_origin_invalid", 0) > 0:
                tag_boost += 0.15
            if tags.get("rpki_old_origin_valid", 0) > 0:
                tag_boost += 0.10
            confidence = min(round(conf_score / 100 + tag_boost, 3), 0.95)
            if confidence == 0.0:
                confidence = 0.50  # floor: Radar flagged it, minimum credence

            victim_asns = item.get("victim_asns") or []
            if item.get("victim_asn") and isinstance(item["victim_asn"], int):
                victim_asns = [item["victim_asn"]]
            affected_asns = list({
                x for x in
                [item.get("hijacker_asn")] + victim_asns
                if x is not None and isinstance(x, int)
            })

            events.append({
                "time":             now,
                "event_type":       "bgp_hijack",
                "severity":         5 if confidence > 0.80 else 4 if confidence > 0.55 else 3,
                "confidence":       confidence,
                "affected_asns":    affected_asns,
                "affected_prefixes": item.get("prefixes", []),
                "affected_regions": item.get("victim_countries", []),
                "source":           "cloudflare-radar",
                "summary":          (
                    f"Hijack: AS{item.get('hijacker_asn')} claiming "
                    f"prefixes from AS{victim_asns}. "
                    f"Confidence: {confidence:.2f}"
                ),
                "raw_signals":      item,
            })

        log.info("radar_bgp_hijacks_fetched", count=len(events))
    except httpx.HTTPStatusError as e:
        log.warning("radar_bgp_hijacks_error", status=e.response.status_code)
    except Exception as e:
        log.error("radar_bgp_hijacks_exception", error=str(e))

    return events

async def fetch_radar_route_leaks(client: httpx.AsyncClient) -> list[dict]:
    """Fetch BGP route leak events from Cloudflare Radar."""
    if not settings.cloudflare_radar_token:
        return []

    events = []
    try:
        resp = await client.get(
            "/bgp/leaks/events",
        )
        resp.raise_for_status()
        data = resp.json()
        now  = datetime.now(tz=timezone.utc)

        for item in data.get("result", {}).get("events", []):
            events.append({
                "time":             now,
                "event_type":       "route_leak",
                "severity":         4,
                "confidence":       0.75,
                "affected_asns":    [
                    x for x in [
                        item.get("leak_asn"),   # primary leaker ASN (Cloudflare Radar field)
                        item.get("leaker_asn"),  # fallback field name variant
                        *(item.get("leak_seg") or []),  # full leak path segment
                    ] if x is not None and isinstance(x, int)
                ],
                "affected_prefixes": item.get("prefixes", []),
                "affected_regions": item.get("countries", []),
                "source":           "cloudflare-radar",
                "summary":          (
                    f"Route leak: AS{item.get('leak_asn') or item.get('leaker_asn')} "
                    f"leak_seg={item.get('leak_seg')} prefixes={item.get('prefix_count',0)}"
                ),
                "raw_signals":      item,
            })

        log.info("radar_route_leaks_fetched", count=len(events))
    except Exception as e:
        log.warning("radar_route_leaks_error", error=str(e))

    return events


# ─────────────────────────────────────────────
# CAIDA IODA collector
# Docs: https://api.ioda.caida.org/
# Combines BGP, darknet telescope, and DNS signals
# Pre-correlated outage detection — high confidence
# ─────────────────────────────────────────────

IODA_BASE = "https://api.ioda.caida.org/v2"

# IODA severity levels map to our 1-5 scale
IODA_SCORE_MAP = {
    "critical": 5,
    "warning":  3,
    "normal":   1,
}


async def fetch_ioda_outages(client: httpx.AsyncClient) -> list[dict]:
    """
    Fetch current internet outages from CAIDA IODA.
    IODA correlates BGP withdrawals, darknet telescope, and active DNS probes.
    This is one of the highest-confidence outage signals available publicly.
    """
    events = []
    now = datetime.now(tz=timezone.utc)
    from_ts = int((now - timedelta(minutes=30)).timestamp())
    until_ts = int(now.timestamp())

    try:
        resp = await client.get(
            f"{IODA_BASE}/signals/raw/country",
            params={
                "from":   from_ts,
                "until":  until_ts,
                "limit":  500,
            }
        )
        resp.raise_for_status()
        data = resp.json()

        for entry in data.get("data", []):
            entity  = entry.get("entity", {})
            alerts  = entry.get("alerts", [])

            if not alerts:
                continue

            country_code = entity.get("code", "")
            region_name  = entity.get("name", "")

            for alert in alerts:
                score = alert.get("score", 0)
                if score < 10:
                    continue

                severity   = 5 if score > 80 else 4 if score > 50 else 3 if score > 20 else 2
                confidence = round(min(score / 100, 0.95), 3)

                events.append({
                    "time":             now,
                    "event_type":       "outage",
                    "severity":         severity,
                    "confidence":       confidence,
                    "affected_asns":    [],
                    "affected_prefixes": [],
                    "affected_regions": [country_code],
                    "source":           "caida-ioda",
                    "summary":          (
                        f"IODA outage signal: {region_name} ({country_code}). "
                        f"Score: {score}. "
                        f"Method: {alert.get('datasource', 'unknown')}"
                    ),
                    "raw_signals":      alert,
                })

        log.info("ioda_outages_fetched", count=len(events))

    except httpx.HTTPStatusError as e:
        log.warning("ioda_fetch_error", status=e.response.status_code)
    except Exception as e:
        log.error("ioda_fetch_exception", error=str(e))

    return events

async def fetch_ioda_asn_outages(client: httpx.AsyncClient) -> list[dict]:
    """
    Fetch IODA outage signals at the ASN level (more granular than country).
    Surfaces which specific ASes are experiencing reachability problems.
    """
    events = []
    now    = datetime.now(tz=timezone.utc)
    from_ts  = int((now - timedelta(minutes=30)).timestamp())
    until_ts = int(now.timestamp())

    try:
        resp = await client.get(
            f"{IODA_BASE}/signals/raw/asn",
            params={"from": from_ts, "until": until_ts, "limit": 100}
        )
        resp.raise_for_status()
        data = resp.json()

        for entry in data.get("data", []):
            entity = entry.get("entity", {})
            alerts = entry.get("alerts", [])
            if not alerts:
                continue

            asn = entity.get("code")
            if not asn:
                continue

            try:
                asn_int = int(str(asn).replace("AS", "").replace("as", ""))
            except ValueError:
                continue

            for alert in alerts:
                score = alert.get("score", 0)
                if score < 10:
                    continue

                severity   = 5 if score > 80 else 4 if score > 50 else 3 if score > 20 else 2
                confidence = round(min(score / 100, 0.95), 3)

                events.append({
                    "time":             now,
                    "event_type":       "outage",
                    "severity":         severity,
                    "confidence":       confidence,
                    "affected_asns":    [asn_int],
                    "affected_prefixes": [],
                    "affected_regions": [],
                    "source":           "caida-ioda-asn",
                    "summary":          (
                        f"IODA ASN outage: AS{asn_int} ({entity.get('name', '')}). "
                        f"Score: {score}. Method: {alert.get('datasource', 'unknown')}"
                    ),
                    "raw_signals":      alert,
                })

        log.info("ioda_asn_outages_fetched", count=len(events))

    except Exception as e:
        log.warning("ioda_asn_fetch_error", error=str(e))

    return events


# ─────────────────────────────────────────────
# PeeringDB collector
# Docs: https://www.peeringdb.com/api/
# Slower cadence — topology data, not real-time
# ─────────────────────────────────────────────

PEERINGDB_BASE = "https://www.peeringdb.com/api"


async def fetch_peeringdb_ixps(client: httpx.AsyncClient) -> list[dict]:
    """
    Fetch IXP list from PeeringDB for topology enrichment.
    Returns list of IXP records to be upserted into Neo4j.
    Runs on slower cadence (hourly) — IXP topology rarely changes.
    """
    ixps = []
    try:
        resp = await client.get(
            f"{PEERINGDB_BASE}/ix",
            params={"depth": 1, "status": "ok"},
        )
        resp.raise_for_status()
        data = resp.json()

        for ix in data.get("data", []):
            ixps.append({
                "id":       ix.get("id"),
                "name":     ix.get("name"),
                "city":     ix.get("city"),
                "country":  ix.get("country"),
                "region":   ix.get("region_continent"),
                "website":  ix.get("website"),
                "tech_email": ix.get("tech_email"),
                "policy_email": ix.get("policy_email"),
            })

        log.info("peeringdb_ixps_fetched", count=len(ixps))

    except Exception as e:
        log.warning("peeringdb_ixp_fetch_error", error=str(e))

    return ixps

# ─────────────────────────────────────────────
# Storage writers
# ─────────────────────────────────────────────

async def write_traffic_metrics(metrics: list[dict]):
    """Bulk-insert traffic metric rows into TimescaleDB."""
    if not metrics:
        return

    pool = await get_pg_pool()
    rows = [
        (
            m["time"],
            m.get("region"),
            m.get("country_code"),
            m.get("asn"),
            m["metric_type"],
            m["value"],
            m["source"],
        )
        for m in metrics
    ]

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO traffic_metrics
                (time, region, country_code, asn, metric_type, value, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            rows,
        )
    log.info("traffic_metrics_written", count=len(rows))


async def write_network_events(events: list[dict]):
    """Insert detected network events into TimescaleDB."""
    if not events:
        return

    import uuid
    pool = await get_pg_pool()
    rows = [
        (
            e["time"],
            str(uuid.uuid4()),
            e["event_type"],
            e.get("severity", 3),
            e.get("confidence", 0.5),
            e.get("affected_asns") or [],
            e.get("affected_prefixes") or [],
            e.get("affected_regions") or [],
            0,   # tech_signal_count
            0,   # community_signal_count
            e.get("confidence", 0.5),
            1,   # source_count
            e["time"],
            None,   # last_updated
            None,   # resolved_at
            e.get("summary"),
            json.dumps(e.get("raw_signals") or {}),
        )
        for e in events
    ]

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO network_events
                (time, event_id, event_type, severity, confidence,
                 affected_asns, affected_prefixes, affected_regions,
                 tech_signal_count, community_signal_count,
                 correlation_score, source_count,
                 first_detected, last_updated, resolved_at,
                 summary, raw_signals)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb)
            """,
            rows,
        )
    log.info("network_events_written", count=len(rows))


async def publish_events_to_redis(events: list[dict]):
    """Publish high-severity events to Redis raw.traffic stream."""
    if not events:
        return

    high_severity = [e for e in events if e.get("severity", 0) >= 3]
    if not high_severity:
        return

    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)

        for e in high_severity:
            await r.xadd("raw.traffic", {
                "event_type":   e["event_type"],
                "severity":     str(e.get("severity", 3)),
                "confidence":   str(e.get("confidence", 0.5)),
                "source":       e.get("source", ""),
                "summary":      (e.get("summary") or "")[:500],
                "affected_asns": json.dumps(e.get("affected_asns") or []),
                "time":         e["time"].isoformat(),
            }, maxlen=5000)

        await r.aclose()
        log.info("redis_traffic_published", count=len(high_severity))

    except Exception as e:
        log.warning("redis_traffic_publish_failed", error=str(e))

# ─────────────────────────────────────────────
# Main collection cycle
# ─────────────────────────────────────────────

# PeeringDB runs hourly — track last run
_last_peeringdb_run: float = 0.0
PEERINGDB_INTERVAL = 3600


async def run_collection_cycle():
    """
    One full traffic collection cycle:
      1. Cloudflare Radar — traffic summary + BGP hijacks + route leaks
      2. CAIDA IODA       — country + ASN outage signals
      3. PeeringDB        — IXP topology (hourly only)
    All writes are concurrent per category.
    """
    global _last_peeringdb_run
    now = time.time()

    log.info("traffic_cycle_start")

    all_metrics: list[dict] = []
    all_events:  list[dict] = []

    # ── Cloudflare Radar ─────────────────────
    radar_headers = {
        "Authorization": f"Bearer {settings.cloudflare_radar_token}",
        "Content-Type":  "application/json",
    }
    async with _make_client(RADAR_BASE, headers=radar_headers) as radar:
        traffic_summary, traffic_location, hijacks, leaks = await asyncio.gather(
            fetch_radar_traffic_summary(radar),
            fetch_radar_traffic_by_location(radar),
            fetch_radar_bgp_hijacks(radar),
            fetch_radar_route_leaks(radar),
            return_exceptions=True,
        )
        if isinstance(traffic_summary, list):
            all_metrics.extend(traffic_summary)
        if isinstance(traffic_location, list):
            all_metrics.extend(traffic_location)
        if isinstance(hijacks, list):
            all_events.extend(hijacks)
        if isinstance(leaks, list):
            all_events.extend(leaks)

    # ── CAIDA IODA ───────────────────────────
    async with _make_client("") as generic:
        country_outages, asn_outages = await asyncio.gather(
            fetch_ioda_outages(generic),
            fetch_ioda_asn_outages(generic),
            return_exceptions=True,
        )
        if isinstance(country_outages, list):
            all_events.extend(country_outages)
        if isinstance(asn_outages, list):
            all_events.extend(asn_outages)

    # ── PeeringDB (hourly) ───────────────────
    if now - _last_peeringdb_run >= PEERINGDB_INTERVAL:
        async with _make_client(PEERINGDB_BASE) as pdb:
            ixps = await fetch_peeringdb_ixps(pdb)
            if ixps:
                log.info("peeringdb_ixp_topology_ready", ixp_count=len(ixps))
                # TODO: upsert into Neo4j in Phase 2
        _last_peeringdb_run = now

    # ── Write to storage concurrently ────────
    await asyncio.gather(
        write_traffic_metrics(all_metrics),
        write_network_events(all_events),
        publish_events_to_redis(all_events),
        return_exceptions=True,
    )

    log.info("traffic_cycle_complete",
             metrics=len(all_metrics),
             events=len(all_events))


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

_running = True


def _handle_shutdown(sig, frame):
    global _running
    log.info("shutdown_signal_received", signal=sig)
    _running = False


async def main():
    global _running

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    log.info("traffic_collector_starting",
             poll_interval=settings.poll_interval,
             radar_enabled=bool(settings.cloudflare_radar_token))

    await get_pg_pool()

    while _running:
        cycle_start = time.time()

        try:
            await run_collection_cycle()
        except Exception as e:
            log.error("traffic_cycle_error", error=str(e), exc_info=True)

        elapsed   = time.time() - cycle_start
        sleep_for = max(0, settings.poll_interval - elapsed)

        log.info("cycle_sleep",
                 elapsed_s=round(elapsed, 1),
                 sleep_s=round(sleep_for, 1))
        await asyncio.sleep(sleep_for)

    log.info("traffic_collector_stopped")


if __name__ == "__main__":
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ]
    )
    asyncio.run(main())
