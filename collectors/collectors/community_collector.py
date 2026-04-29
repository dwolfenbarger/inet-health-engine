"""
collectors/community_collector.py

Community signal collector — human intelligence layer.
Sources:
  - Reddit (PRAW)         — r/networking, r/sysadmin, r/netsec, r/ipv6
  - X / Twitter (API v2) — filtered stream: BGP, outage, hijack keywords
  - NANOG archive         — mailing list archive scrape (mailman)
  - Hacker News           — Algolia search API
  - StatusPage feeds      — RSS/API for top 50 services

Responsibilities:
  - Poll/stream each source on cadence
  - Extract network entities (ASNs, prefixes, org names) via regex + NLP
  - Score urgency and sentiment per post/message
  - Normalize to CommunitySignal model
  - Write to Elasticsearch (community-signals index)
  - Publish high-urgency signals to Redis stream (raw.community)
  - Correlate against recent BGP anomalies in TimescaleDB

Run as:
    python -m collectors.community_collector
"""

import asyncio
import hashlib
import json
import re
import os
import signal
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.config import settings
from collectors.db import get_pg_pool

log = structlog.get_logger("community_collector")

# ─────────────────────────────────────────────
# Entity extraction — regex-based NLP
# Pulls AS numbers, prefixes, and keywords
# from raw community text without spaCy dep
# (spaCy added in Phase 3 for full NER)
# ─────────────────────────────────────────────

# BGP / network urgency keywords — weighted
URGENCY_KEYWORDS = {
    # Critical signals — weight 3
    "bgp hijack": 3, "route hijack": 3, "prefix hijack": 3,
    "full table drop": 3, "routing table": 3, "bgp withdrawal": 3,
    "major outage": 3, "complete outage": 3, "total loss": 3,
    "blackhole": 3, "null route": 3, "route leak": 3,
    # High signals — weight 2
    "bgp": 2, "outage": 2, "unreachable": 2, "down": 2,
    "flapping": 2, "prepend": 2, "as path": 2, "prefix": 2,
    "peering": 2, "transit": 2, "ix down": 2, "ixp": 2,
    "rpki": 2, "roa": 2, "route origin": 2,
    # Medium signals — weight 1
    "latency": 1, "packet loss": 1, "degraded": 1, "slow": 1,
    "dns": 1, "traceroute": 1, "mtr": 1, "looking glass": 1,
    "fiber cut": 1, "maintenance": 1, "noc": 1,
}

# Regex patterns for network entity extraction
RE_ASN        = re.compile(r'\bAS(\d{1,10})\b', re.IGNORECASE)
RE_ASN_BARE   = re.compile(r'\basn[:\s#]?(\d{1,10})\b', re.IGNORECASE)
RE_ASN_HASH   = re.compile(r'#AS(\d{1,10})\b', re.IGNORECASE)  # Mastodon hashtag form
RE_HTML_TAG   = re.compile(r'<[^>]+>')  # strip HTML from Mastodon posts
RE_PREFIX_V4  = re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})\b')
RE_PREFIX_V6  = re.compile(r'\b([0-9a-fA-F:]{3,39}/\d{1,3})\b')
RE_IP_V4      = re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b')

# Known network org names to detect
NETWORK_ORGS = [
    "cloudflare", "google", "amazon", "aws", "azure", "microsoft",
    "akamai", "fastly", "meta", "facebook", "hurricane electric",
    "lumen", "centurylink", "cogent", "tata", "ntt", "level3",
    "comcast", "verizon", "at&t", "att", "charter", "spectrum",
    "zayo", "pccw", "telia", "dtag", "deutsche telekom",
    "sprint", "t-mobile", "telecom italia", "vodafone", "orange",
]

# Sentiment classification word lists
RESOLUTION_WORDS = {
    "resolved", "fixed", "restored", "back up", "recovered",
    "all clear", "normal", "stable", "working again", "mitigated"
}
URGENCY_WORDS = {
    "urgent", "critical", "major", "severe", "emergency",
    "immediately", "asap", "down", "broken", "failing", "failed"
}


def extract_entities(text: str) -> dict:
    """
    Extract network entities from free text.
    Returns dict with asns, prefixes, ips, orgs found.
    """
    text_lower = text.lower()

    # Strip HTML tags (Mastodon posts arrive as HTML)
    text = RE_HTML_TAG.sub(" ", text)
    text_lower = text.lower()

    asns = []
    for pattern in (RE_ASN, RE_ASN_BARE, RE_ASN_HASH):
        for m in pattern.finditer(text):
            try:
                asns.append(int(m.group(1)))
            except ValueError:
                pass

    prefixes = list(set(
        RE_PREFIX_V4.findall(text) + RE_PREFIX_V6.findall(text)
    ))

    ips = [ip for ip in RE_IP_V4.findall(text)
           if not ip.startswith(("10.", "192.168.", "172."))]

    orgs = [org for org in NETWORK_ORGS if org in text_lower]

    return {
        "asns":     list(set(asns)),
        "prefixes": prefixes[:10],   # cap at 10
        "ips":      ips[:10],
        "orgs":     list(set(orgs)),
    }


def score_urgency(text: str) -> float:
    """
    Score urgency of a post 0.0-1.0 based on keyword presence.
    Higher = more operationally urgent.
    """
    text_lower = text.lower()
    score = 0
    max_possible = 20  # normalization ceiling

    for kw, weight in URGENCY_KEYWORDS.items():
        if kw in text_lower:
            score += weight

    return round(min(score / max_possible, 1.0), 3)


def classify_sentiment(text: str) -> str:
    """
    Classify post sentiment as: urgent | informational | resolved | question
    """
    text_lower = text.lower()

    if any(w in text_lower for w in RESOLUTION_WORDS):
        return "resolved"
    if any(w in text_lower for w in URGENCY_WORDS):
        return "urgent"
    if text.strip().endswith("?") or "anyone else" in text_lower or "is it just me" in text_lower:
        return "question"
    return "informational"


def make_signal_id(source: str, url_or_id: str) -> str:
    """Deterministic dedup ID from source + content identifier."""
    return hashlib.sha256(f"{source}:{url_or_id}".encode()).hexdigest()[:16]

# ─────────────────────────────────────────────
# Reddit collector
# Uses PRAW (Python Reddit API Wrapper)
# Subreddits: networking, sysadmin, netsec, ipv6
# ─────────────────────────────────────────────

REDDIT_SUBREDDITS = [
    "networking",   # primary: BGP, routing, peering
    "sysadmin",     # NOC/ops: outage reports, maintenance
    "netsec",       # security: hijacks, RPKI, DDoS
    "ipv6",         # IPv6 routing events
    "isp",          # ISP outages, peering disputes
    "devops",       # CDN/cloud routing issues
]

# Minimum urgency score to store a post
REDDIT_URGENCY_THRESHOLD = 0.1


async def collect_reddit() -> list[dict]:
    """
    Pull new posts from network-focused subreddits via the public .json API.
    No credentials required — Reddit exposes .json feeds publicly.
    Respects rate limits with 1s delay between subreddit fetches.
    """
    signals = []
    now = datetime.now(tz=timezone.utc)

    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=True,
        headers={"User-Agent": settings.reddit_user_agent},
    ) as client:
        for sub in REDDIT_SUBREDDITS:
            try:
                resp = await client.get(
                    f"https://www.reddit.com/r/{sub}/new.json",
                    params={"limit": 25, "sort": "new"},
                )
                resp.raise_for_status()
                data = resp.json()

                for post in data.get("data", {}).get("children", []):
                    pd = post.get("data", {})
                    full_text = f"{pd.get('title', '')} {pd.get('selftext', '')}"
                    urgency   = score_urgency(full_text)

                    if urgency < REDDIT_URGENCY_THRESHOLD:
                        continue

                    signals.append({
                        "signal_id":    make_signal_id("reddit", pd.get("id", "")),
                        "source":       "reddit",
                        "subreddit":    sub,
                        "title":        pd.get("title", "")[:500],
                        "body":         pd.get("selftext", "")[:2000],
                        "url":          f"https://reddit.com{pd.get('permalink', '')}",
                        "author":       pd.get("author", "[deleted]"),
                        "score":        pd.get("score", 0),
                        "num_comments": pd.get("num_comments", 0),
                        "urgency_score": urgency,
                        "sentiment":    classify_sentiment(full_text),
                        "entities":     extract_entities(full_text),
                        "collected_at": now.isoformat(),
                        "post_time":    datetime.fromtimestamp(
                                            pd.get("created_utc", 0), tz=timezone.utc
                                        ).isoformat(),
                    })

                # Be polite — 1s between subreddits
                await asyncio.sleep(1.0)

            except Exception as e:
                log.warning("reddit_fetch_error", sub=sub, error=str(e))

    log.info("reddit_collected", signals=len(signals), subreddits=len(REDDIT_SUBREDDITS))
    return signals

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def collect_x_recent(client: httpx.AsyncClient) -> list[dict]:
    """
    Search recent X posts (last 15 min) for BGP / network events.
    Uses search/recent endpoint — doesn't require elevated stream access.
    """
    if not settings.x_bearer_token:
        log.warning("x_token_missing", msg="Skipping X — no bearer token")
        return _x_stub()

    signals = []
    query = (
        "(#BGP OR #outage OR #routeleak OR bgp OR \"route leak\" OR \"bgp hijack\" "
        "OR #networkdown OR \"prefix withdrawn\") lang:en -is:retweet"
    )

    try:
        resp = await client.get(
            f"{X_API_BASE}/tweets/search/recent",
            params={
                "query":        query,
                "max_results":  50,
                "tweet.fields": "created_at,author_id,public_metrics,entities",
                "expansions":   "author_id",
                "user.fields":  "username,name,verified",
                "start_time":   (
                    datetime.now(tz=timezone.utc) - timedelta(minutes=15)
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            headers={"Authorization": f"Bearer {settings.x_bearer_token}"},
        )
        resp.raise_for_status()
        data = resp.json()

        # Build user lookup from includes
        users = {
            u["id"]: u
            for u in data.get("includes", {}).get("users", [])
        }

        for tweet in data.get("data", []):
            text      = tweet.get("text", "")
            urgency   = score_urgency(text)

            if urgency < X_URGENCY_THRESHOLD:
                continue

            entities  = extract_entities(text)
            sentiment = classify_sentiment(text)
            metrics   = tweet.get("public_metrics", {})
            author    = users.get(tweet.get("author_id", ""), {})
            now       = datetime.now(tz=timezone.utc)

            signals.append({
                "signal_id":     make_signal_id("x", tweet["id"]),
                "source":        "x",
                "tweet_id":      tweet["id"],
                "text":          text[:1000],
                "url":           f"https://x.com/i/web/status/{tweet['id']}",
                "author":        author.get("username", "unknown"),
                "author_verified": author.get("verified", False),
                "retweets":      metrics.get("retweet_count", 0),
                "likes":         metrics.get("like_count", 0),
                "replies":       metrics.get("reply_count", 0),
                "urgency_score": urgency,
                "sentiment":     sentiment,
                "entities":      entities,
                "collected_at":  now.isoformat(),
                "post_time":     tweet.get("created_at", now.isoformat()),
            })

        log.info("x_collected", signals=len(signals))

    except httpx.HTTPStatusError as e:
        log.warning("x_api_error", status=e.response.status_code)
        if e.response.status_code == 401:
            log.error("x_auth_failed", msg="Check X bearer token")
    except Exception as e:
        log.error("x_collection_error", error=str(e))

    return signals


def _x_stub() -> list[dict]:
    """Synthetic X/Twitter signals for pipeline testing."""
    now = datetime.now(tz=timezone.utc)
    return [
        {
            "signal_id":      make_signal_id("x", "xstub001"),
            "source":         "x",
            "tweet_id":       "xstub001",
            "text":           "#BGP alert: AS13335 (Cloudflare) showing origin change on 1.1.1.0/24. Possible hijack. Monitoring. #networking #RPKI",
            "url":            "https://x.com/i/web/status/xstub001",
            "author":         "bgpmon_stub",
            "author_verified": False,
            "retweets":       34,
            "likes":          89,
            "replies":        12,
            "urgency_score":  score_urgency("#BGP AS13335 Cloudflare origin change 1.1.1.0/24 hijack RPKI"),
            "sentiment":      "urgent",
            "entities":       extract_entities("AS13335 Cloudflare 1.1.1.0/24 hijack RPKI"),
            "collected_at":   now.isoformat(),
            "post_time":      now.isoformat(),
        },
        {
            "signal_id":      make_signal_id("x", "xstub002"),
            "source":         "x",
            "tweet_id":       "xstub002",
            "text":           "Route leak detected: AS8075 (Microsoft) prefixes being announced by AS32934 (Meta). Full BGP table impact unclear. #routeleak #BGP",
            "url":            "https://x.com/i/web/status/xstub002",
            "author":         "routemonitor_stub",
            "author_verified": True,
            "retweets":       67,
            "likes":          201,
            "replies":        28,
            "urgency_score":  score_urgency("route leak AS8075 Microsoft prefixes AS32934 Meta BGP table"),
            "sentiment":      "urgent",
            "entities":       extract_entities("AS8075 Microsoft AS32934 Meta route leak BGP"),
            "collected_at":   now.isoformat(),
            "post_time":      now.isoformat(),
        },
    ]

# ─────────────────────────────────────────────
# NANOG mailing list archive scraper
# Archive: https://mailman.nanog.org/pipermail/nanog/
# Highest-quality operator signal — actual NOC staff
# ─────────────────────────────────────────────

NANOG_ARCHIVE_BASE = "https://mailman.nanog.org/pipermail/nanog"
NANOG_URGENCY_THRESHOLD = 0.2


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
async def collect_nanog(client: httpx.AsyncClient) -> list[dict]:
    """
    Scrape NANOG mailing list archive for current month.
    Parses subject lines and message bodies for network events.
    Respects rate limits — single fetch per cycle.
    """
    signals = []
    now = datetime.now(tz=timezone.utc)
    month_str = now.strftime("%Y-%B")  # e.g. 2026-April

    try:
        from bs4 import BeautifulSoup  # type: ignore

        # Fetch thread index for current month
        resp = await client.get(
            f"{NANOG_ARCHIVE_BASE}/{month_str}/",
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot/0.1; +https://github.com/inet-health)"},
            timeout=20,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Parse thread links from mailman index
        threads = []
        for link in soup.select("ul li a[href$='.html']"):
            subject = link.get_text(strip=True)
            href    = link.get("href", "")
            urgency = score_urgency(subject)
            if urgency >= NANOG_URGENCY_THRESHOLD:
                threads.append((subject, href, urgency))

        # Fetch top N most urgent threads
        threads.sort(key=lambda x: x[2], reverse=True)
        for subject, href, urgency in threads[:10]:
            try:
                msg_url  = f"{NANOG_ARCHIVE_BASE}/{month_str}/{href}"
                msg_resp = await client.get(msg_url, timeout=15)
                msg_resp.raise_for_status()
                msg_soup = BeautifulSoup(msg_resp.text, "html.parser")

                # Extract message body — mailman wraps in <pre>
                pre = msg_soup.find("pre")
                body = pre.get_text()[:3000] if pre else ""

                full_text = f"{subject} {body}"
                entities  = extract_entities(full_text)
                sentiment = classify_sentiment(full_text)

                # Try to extract From: header
                author = "nanog-list"
                from_match = re.search(r'From:\s+(.+?)(?:\n|\r)', body)
                if from_match:
                    author = from_match.group(1).strip()[:100]

                signals.append({
                    "signal_id":    make_signal_id("nanog", href),
                    "source":       "nanog",
                    "subject":      subject[:500],
                    "body":         body[:2000],
                    "url":          msg_url,
                    "author":       author,
                    "urgency_score": urgency,
                    "sentiment":    classify_sentiment(full_text),
                    "entities":     entities,
                    "collected_at": now.isoformat(),
                    "post_time":    now.isoformat(),
                })

                # Be polite — small delay between fetches
                await asyncio.sleep(1.5)

            except Exception as e:
                log.warning("nanog_thread_fetch_error", href=href, error=str(e))

        log.info("nanog_collected", threads_checked=len(threads), signals=len(signals))

    except ImportError:
        log.warning("bs4_not_installed", msg="Using NANOG stub")
        return _nanog_stub()
    except Exception as e:
        log.warning("nanog_collection_error", error=str(e))
        return _nanog_stub()

    return signals


def _nanog_stub() -> list[dict]:
    """Synthetic NANOG signals for pipeline testing."""
    now = datetime.now(tz=timezone.utc)
    return [
        {
            "signal_id":    make_signal_id("nanog", "stub-nanog-001"),
            "source":       "nanog",
            "subject":      "[NANOG] BGP origin AS change observed on major DNS anycast prefixes",
            "body":         (
                "From: John Smith <jsmith@isp.example>\n"
                "We are observing unexpected BGP origin AS changes on 1.1.1.0/24 and "
                "8.8.8.0/24. The new origin AS does not match RPKI ROA. "
                "AS13335 and AS15169 prefixes affected. "
                "Multiple upstream providers confirming. Anyone else seeing this?"
            ),
            "url":          "https://mailman.nanog.org/pipermail/nanog/2026-April/stub001.html",
            "author":       "John Smith <jsmith@isp.example>",
            "urgency_score": score_urgency("BGP origin AS change DNS anycast prefixes 1.1.1.0/24 RPKI ROA AS13335 AS15169"),
            "sentiment":    "urgent",
            "entities":     extract_entities("AS13335 AS15169 1.1.1.0/24 8.8.8.0/24 BGP origin RPKI ROA"),
            "collected_at": now.isoformat(),
            "post_time":    now.isoformat(),
        },
    ]

# ─────────────────────────────────────────────
# Hacker News collector
# Algolia API — fast, no auth required
# High signal-to-noise for major internet events
# ─────────────────────────────────────────────

HN_API_BASE  = "https://hn.algolia.com/api/v1"
HN_THRESHOLD = 0.15


async def collect_hackernews(client: httpx.AsyncClient) -> list[dict]:
    """
    Search HN for network/internet posts in last 2 hours.
    Algolia API requires no auth and is very fast.
    """
    signals = []
    now     = datetime.now(tz=timezone.utc)
    cutoff  = int((now - timedelta(hours=2)).timestamp())

    queries = ["BGP", "internet outage", "route leak", "network outage", "DNS outage"]

    seen_ids = set()
    for query in queries:
        try:
            resp = await client.get(
                f"{HN_API_BASE}/search_by_date",
                params={
                    "query":           query,
                    "tags":            "(story,comment)",
                    "numericFilters":  f"created_at_i>{cutoff}",
                    "hitsPerPage":     20,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            for hit in data.get("hits", []):
                story_id = str(hit.get("objectID", ""))
                if story_id in seen_ids:
                    continue
                seen_ids.add(story_id)

                text     = f"{hit.get('title', '')} {hit.get('comment_text', '') or hit.get('story_text', '')}"
                urgency  = score_urgency(text)

                if urgency < HN_THRESHOLD:
                    continue

                entities  = extract_entities(text)
                sentiment = classify_sentiment(text)

                signals.append({
                    "signal_id":    make_signal_id("hn", story_id),
                    "source":       "hackernews",
                    "story_id":     story_id,
                    "title":        hit.get("title", "")[:500],
                    "body":         text[:2000],
                    "url":          hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
                    "author":       hit.get("author", "unknown"),
                    "hn_points":    hit.get("points", 0),
                    "hn_comments":  hit.get("num_comments", 0),
                    "urgency_score": urgency,
                    "sentiment":    sentiment,
                    "entities":     entities,
                    "collected_at": now.isoformat(),
                    "post_time":    hit.get("created_at", now.isoformat()),
                })

        except Exception as e:
            log.warning("hn_query_error", query=query, error=str(e))

    log.info("hn_collected", signals=len(signals))
    return signals


# ─────────────────────────────────────────────

# ────────────────────────────────────────────────────────
# Mastodon federated timeline collector
# Polls #bgp #routeleak #outage #rpki tags across 3 major
# instances. No authentication required (public timelines).
# This is the free replacement for X/Twitter community signal.
# ────────────────────────────────────────────────────────

MASTODON_INSTANCES = [
    "mastodon.social",
    "fosstodon.org",
    "hachyderm.io",
]
MASTODON_TAGS = ["bgp", "routeleak", "outage", "rpki", "networksecurity"]
MASTODON_MIN_URGENCY = 0.15


async def collect_mastodon(client: httpx.AsyncClient) -> list[dict]:
    """
    Collect BGP/network posts from Mastodon federated timelines.
    Polls 5 hashtags across 3 major instances.
    Returns normalized CommunitySignal-compatible dicts.
    """
    signals = []
    now = datetime.now(tz=timezone.utc)
    seen_ids: set[str] = set()

    for instance in MASTODON_INSTANCES:
        for tag in MASTODON_TAGS:
            try:
                resp = await client.get(
                    f"https://{instance}/api/v1/timelines/tag/{tag}",
                    params={"limit": 20},
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                posts = resp.json()
                if not isinstance(posts, list):
                    continue

                for post in posts:
                    post_id = str(post.get("id", ""))
                    uri     = post.get("uri", "")
                    # Deduplicate across instances (federated posts appear on multiple)
                    dedup_key = uri or post_id
                    if dedup_key in seen_ids:
                        continue
                    seen_ids.add(dedup_key)

                    # Content is HTML - strip tags before processing
                    raw_html = post.get("content", "")
                    text     = RE_HTML_TAG.sub(" ", raw_html).strip()
                    if not text:
                        continue

                    urgency = score_urgency(text)
                    if urgency < MASTODON_MIN_URGENCY:
                        continue

                    created_at = post.get("created_at", now.isoformat())

                    signals.append({
                        "signal_id":    make_signal_id("mastodon", dedup_key),
                        "source":       "mastodon",
                        "provider":     instance,
                        "tag":          tag,
                        "title":        text[:200],
                        "body":         text[:2000],
                        "url":          post.get("url", uri),
                        "author":       post.get("account", {}).get("acct", "unknown"),
                        "urgency_score": urgency,
                        "sentiment":    classify_sentiment(text),
                        "entities":     extract_entities(raw_html + " " + text),
                        "collected_at": now.isoformat(),
                        "post_time":    created_at,
                    })

            except Exception as e:
                log.debug("mastodon_fetch_error", instance=instance, tag=tag, error=str(e))

    log.info("mastodon_collected", signals=len(signals), deduped=len(seen_ids))
    return signals


# StatusPage RSS collector
# Polls official status pages for top services
# ─────────────────────────────────────────────

STATUS_PAGES = [
    {"name": "Cloudflare",   "url": "https://www.cloudflarestatus.com/api/v2/incidents.json"},
    {"name": "AWS",          "url": "https://health.aws.amazon.com/public/currentevents"},
    {"name": "Google Cloud", "url": "https://status.cloud.google.com/incidents.json"},
    {"name": "Fastly",       "url": "https://status.fastly.com/api/v2/incidents.json"},
    {"name": "GitHub",       "url": "https://kctbh9vrtdwd.statuspage.io/api/v2/incidents.json"},
    {"name": "Datadog",      "url": "https://status.datadoghq.com/api/v2/incidents.json"},
]

STATUS_SEVERITY_MAP = {
    "critical":             5,
    "major_outage":         5,
    "major":                4,
    "partial_outage":       3,
    "degraded_performance": 2,
    "investigating":        2,
    "under_maintenance":    1,
    "resolved":             1,
}


async def collect_statuspages(client: httpx.AsyncClient) -> list[dict]:
    """
    Poll official statuspage.io API endpoints for active incidents.
    These are authoritative — when a service self-reports, it's confirmed.
    """
    signals = []
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(hours=6)

    for sp in STATUS_PAGES:
        try:
            resp = await client.get(sp["url"], timeout=10)
            resp.raise_for_status()
            data = resp.json()

            # Normalise response shape: statuspage.io returns {"incidents":[...]},
            # but AWS Health and GCP Status APIs return a bare list at the top level.
            if isinstance(data, list):
                incidents = data
            else:
                incidents = data.get("incidents", [])
            for inc in incidents:
                # Skip old resolved incidents
                updated = inc.get("updated_at", "")
                if updated:
                    try:
                        upd_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        if upd_dt < cutoff:
                            continue
                    except ValueError:
                        pass

                impact   = inc.get("impact", "none")
                severity = STATUS_SEVERITY_MAP.get(impact, 1)
                if severity < 2:
                    continue

                title = inc.get("name", "")
                body  = " ".join(
                    u.get("body", "") for u in inc.get("incident_updates", [])
                )[:2000]
                full_text = f"{title} {body} {sp['name']}"

                signals.append({
                    "signal_id":    make_signal_id("statuspage", inc.get("id", title)),
                    "source":       "statuspage",
                    "provider":     sp["name"],
                    "incident_id":  inc.get("id", ""),
                    "title":        title[:500],
                    "body":         body,
                    "url":          inc.get("shortlink", sp["url"]),
                    "status":       inc.get("status", "unknown"),
                    "impact":       impact,
                    "severity":     severity,
                    "urgency_score": severity / 5.0,
                    "sentiment":    "resolved" if inc.get("status") == "resolved" else "urgent",
                    "entities":     extract_entities(full_text),
                    "collected_at": now.isoformat(),
                    "post_time":    inc.get("created_at", now.isoformat()),
                })

        except Exception as e:
            log.warning("statuspage_fetch_error", provider=sp["name"], error=str(e))

    log.info("statuspages_collected", signals=len(signals))
    return signals

# ─────────────────────────────────────────────
# Correlation engine
# Links community signals to active BGP anomalies
# by matching extracted ASNs and prefixes
# ─────────────────────────────────────────────

async def correlate_with_bgp_anomalies(signals: list[dict]) -> list[dict]:
    """
    For each community signal, check if its extracted entities
    match any active BGP anomalies in TimescaleDB.
    Enriches signal with correlation_score and matched_anomalies.
    """
    if not signals:
        return signals

    try:
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            # Pull anomalies from last 2 hours
            rows = await conn.fetch("""
                SELECT event_id, event_type, affected_prefix,
                       origin_asn, expected_asn, severity, confidence
                FROM bgp_anomalies
                WHERE time > NOW() - INTERVAL '2 hours'
                ORDER BY time DESC
            """)
            active_anomalies = [dict(r) for r in rows]

    except Exception as e:
        log.warning("correlation_db_error", error=str(e))
        active_anomalies = []

    for sig in signals:
        entities = sig.get("entities", {})
        sig_asns     = set(entities.get("asns", []))
        sig_prefixes = set(entities.get("prefixes", []))

        matched = []
        for anom in active_anomalies:
            anom_asns = {
                a for a in [anom.get("origin_asn"), anom.get("expected_asn")]
                if a is not None
            }
            anom_prefix = anom.get("affected_prefix", "")

            asn_match    = bool(sig_asns & anom_asns)
            prefix_match = anom_prefix in sig_prefixes if anom_prefix else False

            if asn_match or prefix_match:
                matched.append({
                    "event_id":   str(anom.get("event_id", "")),
                    "event_type": anom.get("event_type"),
                    "prefix":     anom_prefix,
                    "severity":   anom.get("severity"),
                    "asn_match":  asn_match,
                    "prefix_match": prefix_match,
                })

        # Correlation score — how strongly this signal aligns with active anomalies
        if matched:
            max_severity = max(m.get("severity", 1) for m in matched)
            correlation  = round(min(len(matched) * 0.2 + max_severity * 0.1, 1.0), 3)
        else:
            correlation = 0.0

        sig["matched_anomalies"] = matched
        sig["correlation_score"] = correlation

    correlated_count = sum(1 for s in signals if s.get("correlation_score", 0) > 0)
    log.info("correlation_complete",
             total=len(signals),
             correlated=correlated_count)

    return signals


# ─────────────────────────────────────────────
# Elasticsearch writer
# ─────────────────────────────────────────────

ES_INDEX  = "community-signals"
ES_URL    = os.getenv("ES_URL", "http://elasticsearch:9200")


async def ensure_es_index(client: httpx.AsyncClient):
    """Create Elasticsearch index with mapping if it doesn't exist."""
    try:
        resp = await client.head(f"{ES_URL}/{ES_INDEX}", timeout=5)
        if resp.status_code == 200:
            return  # Already exists
    except Exception:
        pass

    mapping = {
        "mappings": {
            "properties": {
                "signal_id":        {"type": "keyword"},
                "source":           {"type": "keyword"},
                "urgency_score":    {"type": "float"},
                "sentiment":        {"type": "keyword"},
                "correlation_score": {"type": "float"},
                "collected_at":     {"type": "date"},
                "post_time":        {"type": "date"},
                "entities": {
                    "properties": {
                        "asns":     {"type": "integer"},
                        "prefixes": {"type": "keyword"},
                        "orgs":     {"type": "keyword"},
                    }
                },
                "title": {"type": "text", "analyzer": "english"},
                "body":  {"type": "text", "analyzer": "english"},
                "matched_anomalies": {"type": "nested"},
            }
        }
    }

    try:
        resp = await client.put(
            f"{ES_URL}/{ES_INDEX}",
            json=mapping, timeout=10
        )
        log.info("es_index_created", index=ES_INDEX, status=resp.status_code)
    except Exception as e:
        log.warning("es_index_create_error", error=str(e))


async def write_to_elasticsearch(signals: list[dict]):
    """Bulk-index community signals into Elasticsearch."""
    if not signals:
        return

    # Build NDJSON bulk body
    bulk_lines = []
    for sig in signals:
        action = {"index": {"_index": ES_INDEX, "_id": sig["signal_id"]}}
        bulk_lines.append(json.dumps(action))
        bulk_lines.append(json.dumps(sig))

    bulk_body = "\n".join(bulk_lines) + "\n"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{ES_URL}/_bulk",
                content=bulk_body,
                headers={"Content-Type": "application/x-ndjson"},
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()
            errors = [i for i in result.get("items", []) if i.get("index", {}).get("error")]
            log.info("es_bulk_indexed",
                     signals=len(signals),
                     errors=len(errors))
    except Exception as e:
        log.error("es_write_error", error=str(e))


async def publish_to_redis_community(signals: list[dict]):
    """Publish high-urgency or correlated signals to Redis raw.community stream."""
    high_value = [
        s for s in signals
        if s.get("urgency_score", 0) >= 0.3 or s.get("correlation_score", 0) > 0
    ]
    if not high_value:
        return

    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)

        for sig in high_value:
            await r.xadd("raw.community", {
                "signal_id":        sig["signal_id"],
                "source":           sig["source"],
                "urgency_score":    str(sig.get("urgency_score", 0)),
                "sentiment":        sig.get("sentiment", ""),
                "correlation_score": str(sig.get("correlation_score", 0)),
                "asns":             json.dumps(sig.get("entities", {}).get("asns", [])),
                "prefixes":         json.dumps(sig.get("entities", {}).get("prefixes", [])),
                "summary":          (sig.get("title") or sig.get("text") or sig.get("subject") or "")[:300],
                "matched_anomalies": str(len(sig.get("matched_anomalies", []))),
            }, maxlen=5000)

        await r.aclose()
        log.info("redis_community_published", count=len(high_value))

    except Exception as e:
        log.warning("redis_community_publish_error", error=str(e))

# ─────────────────────────────────────────────
# Main collection cycle + entry point
# ─────────────────────────────────────────────

_last_nanog_run: float  = 0.0
NANOG_INTERVAL:  int    = 600   # 10 minutes — be polite to mailman


async def run_collection_cycle():
    """
    One full community collection cycle:
      1. Reddit      — always (5-min cadence)
      2. X           — always (5-min cadence)
      3. HN          — always (fast Algolia API)
      4. StatusPages — always (authoritative incidents)
      5. NANOG       — every 10 min (scrape, be polite)
      6. Correlate all signals against active BGP anomalies
      7. Write to Elasticsearch + Redis
    """
    global _last_nanog_run
    now = time.time()

    log.info("community_cycle_start")
    all_signals: list[dict] = []

    # ── Concurrent fast collectors ────────────
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        results = await asyncio.gather(
            collect_reddit(),
            collect_x_recent(client),
            collect_hackernews(client),
            collect_statuspages(client),
            collect_mastodon(client),
            return_exceptions=True,
        )

    for r in results:
        if isinstance(r, list):
            all_signals.extend(r)
        elif isinstance(r, Exception):
            log.warning("collector_exception", error=str(r))

    # ── NANOG — rate-limited ──────────────────
    if now - _last_nanog_run >= NANOG_INTERVAL:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                nanog_signals = await collect_nanog(client)
                all_signals.extend(nanog_signals)
                _last_nanog_run = now
        except Exception as e:
            log.warning("nanog_cycle_error", error=str(e))

    log.info("community_raw_collected", total=len(all_signals))

    if not all_signals:
        return

    # ── Correlate against BGP anomalies ──────
    all_signals = await correlate_with_bgp_anomalies(all_signals)

    # ── Write to storage ─────────────────────
    await asyncio.gather(
        write_to_elasticsearch(all_signals),
        publish_to_redis_community(all_signals),
        return_exceptions=True,
    )

    # Summary
    correlated = sum(1 for s in all_signals if s.get("correlation_score", 0) > 0)
    urgent     = sum(1 for s in all_signals if s.get("urgency_score", 0) >= 0.3)
    log.info("community_cycle_complete",
             total=len(all_signals),
             urgent=urgent,
             correlated_to_bgp=correlated)


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

    log.info("community_collector_starting",
             poll_interval=settings.poll_interval,
             reddit_enabled=bool(settings.reddit_client_id),
             x_enabled=bool(settings.x_bearer_token))

    await get_pg_pool()

    while _running:
        cycle_start = time.time()

        try:
            await run_collection_cycle()
        except Exception as e:
            log.error("community_cycle_error", error=str(e), exc_info=True)

        elapsed   = time.time() - cycle_start
        sleep_for = max(0, settings.poll_interval - elapsed)

        log.info("cycle_sleep",
                 elapsed_s=round(elapsed, 1),
                 sleep_s=round(sleep_for, 1))
        await asyncio.sleep(sleep_for)

    log.info("community_collector_stopped")


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
