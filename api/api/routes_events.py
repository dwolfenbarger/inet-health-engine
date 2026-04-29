"""
api/routes/events.py
Network events + community signals endpoints.
Joins TimescaleDB network_events with Elasticsearch community-signals.
"""

import json
from fastapi import APIRouter, Query
from typing import Optional
from api.deps import get_pg_pool, get_es

router = APIRouter(prefix="/events", tags=["events"])

ES_INDEX = "community-signals"


@router.get("")
async def network_events(
    limit:       int           = Query(50,  ge=1, le=200),
    severity_min: int          = Query(1,   ge=1, le=5),
    event_type:  Optional[str] = Query(None),
    hours:       int           = Query(24,  ge=1, le=168),
):
    """
    Correlated network events from TimescaleDB.
    Ordered by severity + confidence descending.
    """
    pool = await get_pg_pool()

    conditions = [
        "time > NOW() - ($1 || ' hours')::INTERVAL",
        "severity >= $2",
    ]
    args = [str(hours), severity_min]
    idx  = 3

    if event_type:
        conditions.append(f"event_type = ${idx}")
        args.append(event_type)
        idx += 1

    rows = await pool.fetch(
        f"""
        SELECT time, event_id, event_type, severity, confidence,
               affected_asns, affected_prefixes, affected_regions,
               tech_signal_count, community_signal_count,
               correlation_score, source_count, summary,
               first_detected, resolved_at
        FROM network_events
        WHERE {' AND '.join(conditions)}
        ORDER BY severity DESC, confidence DESC, time DESC
        LIMIT ${idx}
        """,
        *args, limit,
    )

    return {
        "count":  len(rows),
        "events": [dict(r) for r in rows],
    }


@router.get("/community")
async def community_signals(
    limit:        int           = Query(50,  ge=1, le=200),
    source:       Optional[str] = Query(None),
    sentiment:    Optional[str] = Query(None),
    urgency_min:  float         = Query(0.0, ge=0.0, le=1.0),
    correlated_only: bool       = Query(False),
    hours:        int           = Query(6,   ge=1, le=72),
):
    """
    Community signals from Elasticsearch.
    Filterable by source, sentiment, urgency, and BGP correlation.
    """
    es = await get_es()

    must = [
        {"range": {"collected_at": {"gte": f"now-{hours}h"}}},
        {"range": {"urgency_score": {"gte": urgency_min}}},
    ]

    if source:
        must.append({"term": {"source": source}})
    if sentiment:
        must.append({"term": {"sentiment": sentiment}})
    if correlated_only:
        must.append({"range": {"correlation_score": {"gt": 0}}})

    try:
        resp = await es.search(
            index=ES_INDEX,
            body={
                "query": {"bool": {"must": must}},
                "sort":  [
                    {"urgency_score":    {"order": "desc"}},
                    {"correlation_score": {"order": "desc"}},
                ],
                "size": limit,
                "_source": {
                    "excludes": ["body"]   # body can be large — exclude from list view
                },
            },
        )

        hits = resp["hits"]["hits"]
        return {
            "count":   len(hits),
            "total":   resp["hits"]["total"]["value"],
            "signals": [h["_source"] for h in hits],
        }

    except Exception as e:
        return {"count": 0, "total": 0, "signals": [], "error": str(e)}


@router.get("/community/{signal_id}")
async def community_signal_detail(signal_id: str):
    """Full community signal document including body text."""
    es = await get_es()
    try:
        resp = await es.get(index=ES_INDEX, id=signal_id)
        return resp["_source"]
    except Exception as e:
        return {"error": str(e)}


@router.get("/feed")
async def unified_event_feed(
    limit: int = Query(50, ge=1, le=100),
    hours: int = Query(2,  ge=1, le=48),
):
    """
    Unified event feed — merges BGP anomalies + community signals
    into a single ranked stream for the NOC live feed panel.
    Sorted by composite urgency score descending.
    """
    pool = await get_pg_pool()
    es   = await get_es()

    import asyncio
    bgp_rows_task = pool.fetch("""
        SELECT
            time,
            event_id::text,
            event_type,
            severity,
            confidence,
            affected_prefix AS detail,
            origin_asn,
            source,
            'bgp' AS feed_type
        FROM bgp_anomalies
        WHERE time > NOW() - ($1 || ' hours')::INTERVAL
        ORDER BY severity DESC, time DESC
        LIMIT 25
    """, str(hours))

    es_task = es.search(
        index=ES_INDEX,
        body={
            "query": {
                "bool": {
                    "must": [
                        {"range": {"collected_at": {"gte": f"now-{hours}h"}}},
                        {"range": {"urgency_score": {"gte": 0.15}}},
                    ]
                }
            },
            "sort":  [{"urgency_score": {"order": "desc"}}],
            "size":  25,
            "_source": ["signal_id","source","urgency_score","sentiment",
                        "correlation_score","entities","matched_anomalies",
                        "title","text","subject","collected_at"],
        },
    )

    bgp_rows, es_resp = await asyncio.gather(bgp_rows_task, es_task, return_exceptions=True)

    feed = []

    if isinstance(bgp_rows, list):
        for r in bgp_rows:
            d = dict(r)
            d["feed_type"]     = "bgp_anomaly"
            d["urgency_score"] = round((d.get("severity", 1) / 5) * d.get("confidence", 0.5), 3)
            feed.append(d)

    if isinstance(es_resp, dict):
        for hit in es_resp["hits"]["hits"]:
            src = hit["_source"]
            src["feed_type"] = "community"
            feed.append(src)

    # Sort unified feed by urgency descending
    feed.sort(key=lambda x: x.get("urgency_score", 0), reverse=True)

    return {
        "count": len(feed[:limit]),
        "feed":  feed[:limit],
    }


@router.get("/community-correlated")
async def correlated_community_signals(
    hours: int   = Query(6, ge=1, le=48),
    limit: int   = Query(30, ge=1, le=100),
):
    """
    Community signals from ES cross-referenced live against RIS anomalies.
    Extracts ASNs + prefixes from signal text, matches against bgp_anomalies,
    returns signals with matched_asns, matched_prefixes, and correlation_score.
    """
    import re
    es   = await get_es()
    pool = await get_pg_pool()

    # 1. Fetch recent community signals from ES
    try:
        resp = await es.search(
            index=ES_INDEX,
            body={
                "query": {"bool": {"must": [
                    {"range": {"collected_at": {"gte": f"now-{hours}h"}}},
                    {"range": {"urgency_score": {"gte": 0.1}}},
                ]}},
                "sort": [{"urgency_score": {"order": "desc"}}],
                "size": limit * 2,   # fetch extra, filter after correlation
            },
        )
        signals = [h["_source"] for h in resp["hits"]["hits"]]
    except Exception:
        signals = []

    if not signals:
        return {"count": 0, "signals": [], "error": "no community signals in ES"}

    # 2. Fetch active RIS anomalies from TimescaleDB
    anom_rows = await pool.fetch("""
        SELECT DISTINCT ON (affected_prefix, origin_asn, event_type)
            event_type, affected_prefix, origin_asn, expected_asn,
            confidence, severity
        FROM bgp_anomalies
        WHERE source LIKE 'ris/%%'
          AND time > NOW() - ($1 || ' hours')::INTERVAL
          AND confidence > 0.5
        ORDER BY affected_prefix, origin_asn, event_type, confidence DESC
        LIMIT 500
    """, str(hours))

    # Index anomalies by ASN and prefix for fast lookup
    anom_by_asn: dict[int, list] = {}
    anom_by_prefix: dict[str, list] = {}
    for a in anom_rows:
        if a["origin_asn"]:
            anom_by_asn.setdefault(a["origin_asn"], []).append(dict(a))
        if a["affected_prefix"]:
            anom_by_prefix.setdefault(a["affected_prefix"], []).append(dict(a))

    # 3. Correlate each signal
    ASN_RE    = re.compile(r'\bAS(\d{1,10})\b', re.IGNORECASE)
    PREFIX_RE = re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})\b')

    enriched = []
    for sig in signals:
        text = f"{sig.get('title','')} {sig.get('body','')}"

        # Extract ASNs and prefixes from signal text
        found_asns    = [int(m) for m in ASN_RE.findall(text)]
        found_prefixes = PREFIX_RE.findall(text)

        matched_anoms = []
        for asn in found_asns:
            matched_anoms.extend(anom_by_asn.get(asn, []))
        for pfx in found_prefixes:
            matched_anoms.extend(anom_by_prefix.get(pfx, []))

        # Deduplicate matches
        seen = set()
        unique_matches = []
        for m in matched_anoms:
            key = (m["event_type"], m["affected_prefix"], m["origin_asn"])
            if key not in seen:
                seen.add(key)
                unique_matches.append(m)

        # Score: urgency × (1 + 0.5*matches) — rewarded for BGP correlation
        base_urgency = float(sig.get("urgency_score", 0))
        correlation  = min(len(unique_matches) * 0.3, 0.9)
        score        = round(base_urgency * (1 + correlation), 3)

        enriched.append({
            "id":               sig.get("id"),
            "source":           sig.get("source"),
            "title":            sig.get("title"),
            "url":              sig.get("url"),
            "collected_at":     sig.get("collected_at"),
            "urgency_score":    base_urgency,
            "correlation_score":correlation,
            "composite_score":  score,
            "sentiment":        sig.get("sentiment"),
            "extracted_asns":   found_asns,
            "extracted_prefixes": found_prefixes,
            "matched_anomalies": unique_matches[:5],
            "match_count":      len(unique_matches),
        })

    # Sort by composite score descending
    enriched.sort(key=lambda x: -x["composite_score"])
    top = [e for e in enriched if e["match_count"] > 0 or e["urgency_score"] > 0.3][:limit]

    return {
        "count":          len(top),
        "hours":          hours,
        "active_anomalies": len(anom_rows),
        "signals":        top,
    }
