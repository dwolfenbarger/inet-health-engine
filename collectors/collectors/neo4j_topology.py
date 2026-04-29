"""
collectors/neo4j_topology.py

AS topology graph builder for Neo4j.
Constructs and maintains the AS relationship graph from:
  - BGP AS path data (inferred peering/transit)
  - PeeringDB (explicit peering policies)
  - CAIDA AS-rank (tier classification)

Graph schema:
  (:AS {asn, name, org, country, tier, health_score})
    -[:PEERS_WITH]->  (:AS)   -- settlement-free peering
    -[:TRANSIT_TO]->  (:AS)   -- customer → provider
    -[:ANNOUNCES]->   (:Prefix {cidr, rpki_status})

Cadence: refreshed every 30 minutes from accumulated BGP data
"""

import asyncio
import json
import time
import signal
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.config import settings
from collectors.db import get_pg_pool

log = structlog.get_logger("neo4j_topology")

TOPOLOGY_INTERVAL = 1800  # 30 minutes

# CAIDA AS-rank API for tier classification
CAIDA_ASRANK_BASE = "https://api.asrank.caida.org/v2/restful"

# Known Tier 1 ASes (settlement-free globally)
TIER_1_ASNS = {
    174,   # Cogent
    701,   # Verizon/UUNet
    1239,  # Sprint
    1299,  # Telia
    2914,  # NTT
    3257,  # GTT
    3320,  # DTAG (Deutsche Telekom)
    3356,  # Lumen/Level3
    3491,  # PCCW
    4134,  # China Telecom
    5511,  # Orange
    6453,  # TATA
    6461,  # Zayo
    6762,  # Telecom Italia
    7018,  # AT&T
    12956, # Telefonica
}

def get_neo4j_driver():
    """Get Neo4j driver from environment config."""
    from neo4j import GraphDatabase
    user, password = settings.neo4j_auth.split("/", 1)
    return GraphDatabase.driver(settings.neo4j_uri, auth=(user, password))


async def fetch_as_paths_from_timescale(hours_back: int = 2) -> list[list[int]]:
    """
    Pull recent AS paths from TimescaleDB bgp_updates.
    Returns list of AS path arrays for topology inference.
    """
    pool = await get_pg_pool()
    rows = await pool.fetch("""
        SELECT DISTINCT as_path
        FROM bgp_updates
        WHERE time > NOW() - ($1 || ' hours')::INTERVAL
          AND array_length(as_path, 1) > 1
          AND origin_asn IS NOT NULL
        LIMIT 5000
    """, str(hours_back))

    paths = []
    for row in rows:
        path = row["as_path"]
        if path and len(path) > 1:
            paths.append(list(path))
    return paths


def infer_relationships(paths: list[list[int]]) -> dict:
    """
    Infer AS relationships from BGP AS paths.
    Uses valley-free routing assumption:
      - Adjacent ASes in a path have a business relationship
      - Tier 1 → Tier 2 = transit
      - Tier 2 → Tier 2 at same level = peering (heuristic)

    Returns dict with:
      - nodes: set of ASNs seen
      - edges: list of (asn_a, asn_b, rel_type)
      - prefix_origins: asn → set of originated prefixes
    """
    nodes    = set()
    edge_map = defaultdict(lambda: defaultdict(int))  # (a, b) → count

    for path in paths:
        # Remove prepended duplicates
        deduped = []
        for asn in path:
            if not deduped or asn != deduped[-1]:
                deduped.append(asn)

        nodes.update(deduped)

        # Adjacent pairs = business relationship
        for i in range(len(deduped) - 1):
            a, b = deduped[i], deduped[i + 1]
            if a != b:
                key = (min(a, b), max(a, b))
                edge_map[key]["count"] += 1

    edges = []
    for (a, b), data in edge_map.items():
        count = data["count"]
        # Classify relationship
        a_tier1 = a in TIER_1_ASNS
        b_tier1 = b in TIER_1_ASNS

        if a_tier1 and b_tier1:
            rel = "PEERS_WITH"   # Tier 1 ↔ Tier 1 = peering
        elif a_tier1 or b_tier1:
            # Tier 1 provides transit to non-Tier 1
            provider = a if a_tier1 else b
            customer = b if a_tier1 else a
            edges.append((customer, provider, "TRANSIT_TO", count))
            continue
        elif count >= 10:
            rel = "PEERS_WITH"   # High frequency = likely peering
        else:
            rel = "CONNECTED_TO" # Unknown relationship

        edges.append((a, b, rel, count))

    return {"nodes": nodes, "edges": edges}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def fetch_as_info_caida(client: httpx.AsyncClient, asns: list[int]) -> dict:
    """
    Fetch AS metadata from CAIDA AS-rank API.
    Returns dict of asn → {name, org, country, rank, cone_size}
    """
    as_info = {}
    # Batch into chunks of 20 (API limit)
    for i in range(0, min(len(asns), 100), 20):
        batch = asns[i:i + 20]
        asn_str = ",".join(str(a) for a in batch)
        try:
            resp = await client.get(
                f"{CAIDA_ASRANK_BASE}/asns",
                params={"asns": asn_str},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            for node in data.get("data", {}).get("asns", {}).get("edges", []):
                asn_data = node.get("node", {})
                asn = asn_data.get("asn")
                if asn:
                    as_info[int(asn)] = {
                        "name":      asn_data.get("asnName", ""),
                        "org":       asn_data.get("organization", {}).get("orgName", ""),
                        "country":   asn_data.get("country", {}).get("iso", ""),
                        "rank":      asn_data.get("rank"),
                        "cone_size": asn_data.get("cone", {}).get("numberAsns", 0),
                        "tier":      1 if int(asn) in TIER_1_ASNS else (
                                     2 if asn_data.get("rank", 999) < 500 else 3
                                     ),
                    }
        except Exception as e:
            log.warning("caida_asrank_error", batch=batch, error=str(e))
    return as_info

async def enrich_with_rdap(asns: list[int], existing: dict) -> dict:
    """
    Enrich AS info using ARIN RDAP for ASNs not found in CAIDA.
    RDAP provides authoritative org name and country from the RIR registry.
    No authentication required. Caps at 50 lookups per cycle to be polite.
    """
    missing = [asn for asn in asns if asn not in existing or not existing[asn].get("name")]
    if not missing:
        return existing

    enriched = dict(existing)
    # Try ARIN first (covers NA), fall back to RIPE (covers EU/rest)
    rdap_bases = [
        "https://rdap.arin.net/registry/autnum/",
        "https://rdap.db.ripe.net/autnum/",
    ]

    async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
        for asn in missing[:50]:
            for base in rdap_bases:
                try:
                    resp = await client.get(f"{base}{asn}")
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    name    = data.get("name", "")
                    handle  = data.get("handle", "")
                    # Extract country from entities
                    country = ""
                    for ent in data.get("entities", []):
                        vcard = ent.get("vcardArray", [])
                        if isinstance(vcard, list) and len(vcard) > 1:
                            for item in vcard[1]:
                                if isinstance(item, list) and item[0] == "adr":
                                    country = item[1].get("cc", "") or ""
                    if name:
                        existing_info = enriched.get(asn, {})
                        enriched[asn] = {
                            "name":      name,
                            "org":       existing_info.get("org", name),
                            "country":   country or existing_info.get("country", ""),
                            "rank":      existing_info.get("rank"),
                            "cone_size": existing_info.get("cone_size", 0),
                            "tier":      existing_info.get("tier", 3),
                            "source":    "rdap",
                        }
                        break  # got a result, no need to try next RIR
                except Exception:
                    continue

    newly_enriched = sum(1 for asn in missing[:50] if asn in enriched and enriched[asn].get("source") == "rdap")
    log.info("rdap_enrichment_complete", checked=len(missing[:50]), enriched=newly_enriched)
    return enriched


def upsert_topology_to_neo4j(nodes: set, edges: list, as_info: dict):
    """
    Write AS topology graph to Neo4j using Cypher MERGE statements.
    Uses MERGE to upsert — safe to run repeatedly.
    """
    driver = get_neo4j_driver()
    try:
        with driver.session() as session:
            # Create constraints and indexes (idempotent)
            session.run("CREATE CONSTRAINT asn_unique IF NOT EXISTS FOR (a:AS) REQUIRE a.asn IS UNIQUE")
            session.run("CREATE INDEX prefix_cidr IF NOT EXISTS FOR (p:Prefix) ON (p.cidr)")

            # Upsert AS nodes
            node_batch = []
            for asn in nodes:
                info = as_info.get(asn, {})
                node_batch.append({
                    "asn":       asn,
                    "name":      info.get("name", f"AS{asn}"),
                    "org":       info.get("org", ""),
                    "country":   info.get("country", ""),
                    "tier":      info.get("tier", 3),
                    "rank":      info.get("rank"),
                    "cone_size": info.get("cone_size", 0),
                    "updated_at": datetime.now(tz=timezone.utc).isoformat(),
                })

            if node_batch:
                session.run("""
                    UNWIND $nodes AS n
                    MERGE (a:AS {asn: n.asn})
                    SET a.name      = n.name,
                        a.org       = n.org,
                        a.country   = n.country,
                        a.tier      = n.tier,
                        a.rank      = n.rank,
                        a.cone_size = n.cone_size,
                        a.updated_at = n.updated_at
                """, nodes=node_batch)
                log.info("neo4j_nodes_upserted", count=len(node_batch))

            # Upsert relationships
            edge_batch = []
            for edge in edges:
                a, b, rel, count = edge
                edge_batch.append({"a": a, "b": b, "rel": rel, "count": count})

            if edge_batch:
                # PEERS_WITH (bidirectional)
                peer_edges = [e for e in edge_batch if e["rel"] == "PEERS_WITH"]
                if peer_edges:
                    session.run("""
                        UNWIND $edges AS e
                        MATCH (a:AS {asn: e.a}), (b:AS {asn: e.b})
                        MERGE (a)-[r:PEERS_WITH]-(b)
                        SET r.path_count = e.count,
                            r.updated_at = timestamp()
                    """, edges=peer_edges)

                # TRANSIT_TO (directional)
                transit_edges = [e for e in edge_batch if e["rel"] == "TRANSIT_TO"]
                if transit_edges:
                    session.run("""
                        UNWIND $edges AS e
                        MATCH (a:AS {asn: e.a}), (b:AS {asn: e.b})
                        MERGE (a)-[r:TRANSIT_TO]->(b)
                        SET r.path_count = e.count,
                            r.updated_at = timestamp()
                    """, edges=transit_edges)

                # CONNECTED_TO (unknown relationship)
                conn_edges = [e for e in edge_batch if e["rel"] == "CONNECTED_TO"]
                if conn_edges:
                    session.run("""
                        UNWIND $edges AS e
                        MATCH (a:AS {asn: e.a}), (b:AS {asn: e.b})
                        MERGE (a)-[r:CONNECTED_TO]-(b)
                        SET r.path_count = e.count,
                            r.updated_at = timestamp()
                    """, edges=conn_edges)

                log.info("neo4j_edges_upserted",
                         peers=len(peer_edges),
                         transit=len(transit_edges),
                         connected=len(conn_edges))

    finally:
        driver.close()


async def run_collection_cycle():
    """One full topology build cycle."""
    log.info("topology_cycle_start")

    # 1. Pull recent AS paths from TimescaleDB
    paths = await fetch_as_paths_from_timescale(hours_back=2)
    if not paths:
        log.info("topology_no_paths")
        return

    # 2. Infer relationships
    topology = infer_relationships(paths)
    nodes    = topology["nodes"]
    edges    = topology["edges"]
    log.info("topology_inferred",
             nodes=len(nodes), edges=len(edges), paths=len(paths))

    # 3. Fetch AS metadata from CAIDA
    as_info = {}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        as_info = await fetch_as_info_caida(client, list(nodes))
    log.info("caida_as_info_fetched", count=len(as_info))

    # 3b. RDAP enrichment for ASNs CAIDA missed
    as_info = await enrich_with_rdap(list(nodes), as_info)

    # 4. Write to Neo4j
    try:
        upsert_topology_to_neo4j(nodes, edges, as_info)
        log.info("neo4j_topology_updated",
                 nodes=len(nodes), edges=len(edges))
    except Exception as e:
        log.error("neo4j_write_error", error=str(e))


_running = True


def _handle_shutdown(sig, frame):
    global _running
    _running = False


async def main():
    global _running
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    log.info("topology_builder_starting", interval_s=TOPOLOGY_INTERVAL)
    await get_pg_pool()

    while _running:
        start = time.time()
        try:
            await run_collection_cycle()
        except Exception as e:
            log.error("topology_cycle_error", error=str(e), exc_info=True)

        elapsed   = time.time() - start
        sleep_for = max(0, TOPOLOGY_INTERVAL - elapsed)
        log.info("topology_cycle_sleep",
                 elapsed_s=round(elapsed, 1),
                 sleep_s=round(sleep_for, 1))
        await asyncio.sleep(sleep_for)


if __name__ == "__main__":
    import structlog
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ])
    asyncio.run(main())
