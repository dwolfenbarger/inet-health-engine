"""
collectors/nlp_engine.py

Phase 3 NLP correlation engine.
Extracts named entities from community signals and links them
to active BGP anomalies with a composite confidence score.

Entity types extracted:
  - ASNs (AS#### patterns + org name → ASN lookup)
  - IP prefixes (CIDR notation)
  - Organization names (Cloudflare, Google, AWS, etc.)
  - Geographic references (countries, cities, regions)
  - Event keywords (hijack, leak, flap, outage, etc.)

Correlation logic:
  - ASN match: community signal mentions same AS as BGP anomaly origin/victim
  - Prefix match: community signal mentions same CIDR as affected prefix
  - Org match: community signal mentions org name that maps to anomaly ASN
  - Keyword boost: urgency keywords increase correlation confidence
  - Temporal weight: recent signals weighted higher than old ones

Run as:
    python -m collectors.nlp_engine
Or imported by community_collector for inline enrichment.
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import structlog

from collectors.config import settings
from collectors.db import get_pg_pool

log = structlog.get_logger("nlp_engine")

# ─────────────────────────────────────────────
# Entity extraction patterns
# ─────────────────────────────────────────────

RE_ASN_FULL   = re.compile(r'\b(?:AS|ASN)\s*(\d{1,10})\b', re.IGNORECASE)
RE_ASN_BARE   = re.compile(r'\basn[:\s#]?(\d{1,10})\b', re.IGNORECASE)
RE_PREFIX_V4  = re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})\b')
RE_PREFIX_V6  = re.compile(r'\b([0-9a-fA-F:]{3,39}/\d{1,3})\b')
RE_COUNTRY    = re.compile(
    r'\b(United States|USA|UK|Germany|France|China|Russia|Japan|'
    r'Brazil|Australia|Canada|India|Netherlands|Sweden|Singapore)\b',
    re.IGNORECASE
)

# ── Org name → ASN mapping ───────────────────
ORG_TO_ASN: dict[str, list[int]] = {
    "cloudflare":        [13335, 209242],
    "google":            [15169, 19527],
    "youtube":           [15169],
    "amazon":            [16509, 14618, 8987],
    "aws":               [16509, 14618],
    "microsoft":         [8075, 3598],
    "azure":             [8075],
    "akamai":            [20940, 16625],
    "meta":              [32934, 54115],
    "facebook":          [32934],
    "instagram":         [32934],
    "fastly":            [54113, 394192],
    "twitter":           [13414, 35995],
    "x.com":             [13414],
    "apple":             [714, 6185],
    "netflix":           [2906, 40027],
    "lumen":             [3356, 11213],
    "centurylink":       [3356],
    "cogent":            [174],
    "ntt":               [2914],
    "tata":              [6453],
    "level3":            [3356],
    "hurricane electric": [6939],
    "he.net":            [6939],
    "zayo":              [6461],
    "comcast":           [7922, 33657],
    "verizon":           [701, 19262],
    "att":               [7018, 12271],
    "at&t":              [7018],
    "charter":           [20115, 33657],
    "spectrum":          [20115],
    "telia":             [1299],
    "dtag":              [3320],
    "deutsche telekom":  [3320],
    "telecom italia":    [3269],
    "vodafone":          [1273, 3209],
    "orange":            [5511],
    "bt":                [2856],
    "sprint":            [1239],
    "t-mobile":          [21928],
    "pccw":              [3491],
    "linx":              [5459],
    "ams-ix":            [1200],
    "de-cix":            [6695],
    "equinix":           [24115],
}

# ── Event keyword taxonomy ────────────────────
EVENT_KEYWORDS = {
    # Routing events
    "hijack":        ("bgp_hijack",       3.0),
    "route hijack":  ("bgp_hijack",       3.0),
    "prefix hijack": ("bgp_hijack",       3.0),
    "route leak":    ("route_leak",       3.0),
    "bgp leak":      ("route_leak",       3.0),
    "leaked route":  ("route_leak",       2.5),
    "flapping":      ("bgp_flap",         2.0),
    "flap":          ("bgp_flap",         2.0),
    "bgp flap":      ("bgp_flap",         2.5),
    "withdrawal":    ("withdrawal_surge", 2.0),
    "withdrawn":     ("withdrawal_surge", 1.5),
    "blackhole":     ("bgp_hijack",       2.5),
    "null route":    ("bgp_hijack",       2.0),

    # Outage signals
    "outage":        ("outage", 2.0),
    "down":          ("outage", 1.5),
    "unreachable":   ("outage", 2.0),
    "packet loss":   ("outage", 1.5),
    "latency spike": ("latency_spike", 1.5),
    "high latency":  ("latency_spike", 1.5),

    # Resolution signals (negative weight)
    "resolved":      ("resolution", -1.0),
    "fixed":         ("resolution", -1.0),
    "restored":      ("resolution", -0.8),
    "stable":        ("resolution", -0.5),
    "all clear":     ("resolution", -1.0),
}

# ─────────────────────────────────────────────
# Core entity extraction
# ─────────────────────────────────────────────

def extract_full_entities(text: str) -> dict:
    """
    Full entity extraction from community signal text.
    Returns structured dict with all entity types.
    """
    text_lower = text.lower()

    # ASNs — direct pattern matching
    asns = set()
    for m in RE_ASN_FULL.finditer(text):
        try:
            asns.add(int(m.group(1)))
        except ValueError:
            pass
    for m in RE_ASN_BARE.finditer(text):
        try:
            asns.add(int(m.group(1)))
        except ValueError:
            pass

    # Org → ASN resolution
    org_names = []
    org_asns  = set()
    for org, org_asn_list in ORG_TO_ASN.items():
        if org in text_lower:
            org_names.append(org)
            org_asns.update(org_asn_list)

    # Merge org-derived ASNs
    all_asns = list(asns | org_asns)

    # Prefixes
    prefixes = list(set(
        RE_PREFIX_V4.findall(text) + RE_PREFIX_V6.findall(text)
    ))

    # Event keyword detection
    detected_events = []
    total_keyword_score = 0.0
    for kw, (event_type, weight) in EVENT_KEYWORDS.items():
        if kw in text_lower:
            detected_events.append({"keyword": kw, "event_type": event_type, "weight": weight})
            total_keyword_score += weight

    # Geographic references
    countries = list(set(m.group(0) for m in RE_COUNTRY.finditer(text)))

    # Compute urgency from keyword score
    urgency = round(min(max(total_keyword_score / 10.0, 0.0), 1.0), 3)

    return {
        "asns":           all_asns,
        "direct_asns":    list(asns),
        "org_asns":       list(org_asns),
        "org_names":      org_names,
        "prefixes":       prefixes[:15],
        "countries":      countries,
        "detected_events": detected_events,
        "keyword_score":  round(total_keyword_score, 3),
        "urgency":        urgency,
    }


def classify_event_type(entities: dict) -> Optional[str]:
    """
    Infer the most likely BGP event type from extracted entities.
    Returns the highest-weight positive event type, or None.
    """
    event_weights: dict[str, float] = {}
    for ev in entities.get("detected_events", []):
        et = ev["event_type"]
        w  = ev["weight"]
        if et == "resolution":
            continue
        event_weights[et] = event_weights.get(et, 0) + w

    if not event_weights:
        return None
    return max(event_weights, key=lambda k: event_weights[k])


def is_resolution(entities: dict) -> bool:
    """True if the signal is a resolution/recovery notification."""
    resolution_score = sum(
        ev["weight"] for ev in entities.get("detected_events", [])
        if ev["event_type"] == "resolution"
    )
    positive_score = sum(
        ev["weight"] for ev in entities.get("detected_events", [])
        if ev["event_type"] != "resolution" and ev["weight"] > 0
    )
    return resolution_score < 0 and abs(resolution_score) > positive_score

# ─────────────────────────────────────────────
# Correlation scoring engine
# ─────────────────────────────────────────────

def compute_correlation_score(
    signal_entities: dict,
    anomaly: dict,
    signal_age_minutes: float = 0.0,
) -> dict:
    """
    Compute correlation score between a community signal and a BGP anomaly.
    Returns a detailed score breakdown.

    Score components:
      - asn_match:    direct ASN overlap (0.0-0.4)
      - prefix_match: CIDR prefix overlap (0.0-0.4)
      - event_match:  event type alignment (0.0-0.2)
      - temporal:     recency bonus (0.0-0.1)
      - resolution:   penalty if signal is a resolution (-0.3)

    Final score is clamped to [0.0, 1.0].
    """
    score      = 0.0
    breakdown  = {}
    reasons    = []

    sig_asns     = set(signal_entities.get("asns", []))
    sig_prefixes = set(signal_entities.get("prefixes", []))
    sig_event    = classify_event_type(signal_entities)

    anom_asns = set(filter(None, [
        anomaly.get("origin_asn"),
        anomaly.get("expected_asn"),
    ]))
    anom_prefix    = anomaly.get("affected_prefix", "")
    anom_event     = anomaly.get("event_type", "")
    anom_severity  = anomaly.get("severity", 1)
    anom_confidence = anomaly.get("confidence", 0.5)

    # ── ASN match ────────────────────────────
    matched_asns = sig_asns & anom_asns
    if matched_asns:
        asn_score = min(len(matched_asns) * 0.2, 0.4)
        score += asn_score
        breakdown["asn_match"] = asn_score
        reasons.append(f"ASN match: {matched_asns}")
    else:
        breakdown["asn_match"] = 0.0

    # ── Prefix match ─────────────────────────
    if anom_prefix and anom_prefix in sig_prefixes:
        prefix_score = 0.4
        score += prefix_score
        breakdown["prefix_match"] = prefix_score
        reasons.append(f"Prefix match: {anom_prefix}")
    else:
        breakdown["prefix_match"] = 0.0

    # ── Event type alignment ─────────────────
    if sig_event and anom_event:
        if sig_event == anom_event:
            event_score = 0.2
        elif sig_event in anom_event or anom_event in sig_event:
            event_score = 0.1
        else:
            event_score = 0.0
        score += event_score
        breakdown["event_match"] = event_score
        if event_score > 0:
            reasons.append(f"Event type: {sig_event} ~ {anom_event}")
    else:
        breakdown["event_match"] = 0.0

    # ── Temporal recency bonus ────────────────
    if signal_age_minutes < 15:
        temporal = 0.1
    elif signal_age_minutes < 60:
        temporal = 0.05
    else:
        temporal = 0.0
    score += temporal
    breakdown["temporal"] = temporal

    # ── Resolution penalty ────────────────────
    if is_resolution(signal_entities):
        score -= 0.3
        breakdown["resolution_penalty"] = -0.3
        reasons.append("Resolution signal — score penalized")

    # ── Severity amplifier ────────────────────
    # High-severity anomalies get a small amplification
    if anom_severity >= 4 and score > 0.2:
        sev_bonus = round((anom_severity - 3) * 0.03, 3)
        score += sev_bonus
        breakdown["severity_bonus"] = sev_bonus

    final = round(max(0.0, min(score, 1.0)), 4)
    breakdown["final"] = final

    return {
        "score":     final,
        "breakdown": breakdown,
        "reasons":   reasons,
        "matched":   final > 0.1,
    }


async def correlate_signals_to_anomalies(
    signals: list[dict],
    lookback_hours: int = 2,
) -> list[dict]:
    """
    Full correlation pass: enrich each signal with NLP entities,
    score against all recent BGP anomalies, attach results.
    """
    if not signals:
        return signals

    # Fetch active anomalies from TimescaleDB
    try:
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT event_id, event_type, affected_prefix,
                       origin_asn, expected_asn, severity, confidence, time
                FROM bgp_anomalies
                WHERE time > NOW() - ($1 || ' hours')::INTERVAL
                ORDER BY severity DESC, time DESC
                LIMIT 200
            """, str(lookback_hours))
            anomalies = [dict(r) for r in rows]
    except Exception as e:
        log.warning("nlp_db_error", error=str(e))
        anomalies = []

    now = datetime.now(tz=timezone.utc)
    enriched_count = 0
    correlated_count = 0

    for sig in signals:
        # Extract full NLP entities
        text = " ".join(filter(None, [
            sig.get("title", ""),
            sig.get("body", ""),
            sig.get("text", ""),
            sig.get("subject", ""),
        ]))

        entities = extract_full_entities(text)
        sig["nlp_entities"] = entities
        sig["inferred_event_type"] = classify_event_type(entities)
        sig["is_resolution"] = is_resolution(entities)

        # Compute age of signal
        post_time_str = sig.get("post_time") or sig.get("collected_at", "")
        try:
            post_dt = datetime.fromisoformat(post_time_str.replace("Z", "+00:00"))
            age_minutes = (now - post_dt).total_seconds() / 60
        except Exception:
            age_minutes = 60.0

        # Score against each anomaly
        best_match = None
        all_matches = []

        for anom in anomalies:
            result = compute_correlation_score(entities, anom, age_minutes)
            if result["matched"]:
                all_matches.append({
                    "event_id":    str(anom.get("event_id", "")),
                    "event_type":  anom.get("event_type"),
                    "prefix":      anom.get("affected_prefix", ""),
                    "severity":    anom.get("severity"),
                    "score":       result["score"],
                    "reasons":     result["reasons"],
                })
                if best_match is None or result["score"] > best_match["score"]:
                    best_match = all_matches[-1]

        sig["matched_anomalies"] = all_matches
        sig["correlation_score"] = best_match["score"] if best_match else 0.0
        sig["best_match_event"]  = best_match["event_type"] if best_match else None

        enriched_count += 1
        if all_matches:
            correlated_count += 1

    log.info("nlp_correlation_complete",
             signals=len(signals),
             enriched=enriched_count,
             correlated=correlated_count,
             anomalies_checked=len(anomalies))

    return signals
