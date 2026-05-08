# inet-health-engine

A world-class internet health and routing intelligence platform for senior network engineers and service provider planners. Combines live BGP ingest, RPKI validation, AS topology mapping, and community signal correlation into a real-time scored view of global internet health — rendered on an animated 3D NOC globe.

![Stack](https://img.shields.io/badge/Python-3.12-blue) ![Stack](https://img.shields.io/badge/React-18-61dafb) ![Stack](https://img.shields.io/badge/Docker-Compose-2496ed) ![Stack](https://img.shields.io/badge/BGP-RIPE_RIS_Live-orange)

---

## What It Does

- **Live BGP analysis** — streams RIPE RIS Live across 5 collectors (rrc00/11/14/16/21), detects hijacks, route leaks, withdrawal surges, and prefix flaps in a 60-second rolling window
- **RPKI validation** — continuous prefix validity checks against the RIPE RPKI validator, Redis-cached
- **AS topology graph** — CAIDA-sourced AS relationships in Neo4j, enriched with PeeringDB IXP data and RDAP org names
- **Community signal correlation** — Reddit (6 subreddits), HN, Mastodon (3 instances), and StatusPage feeds cross-correlated with active BGP anomalies
- **Internet health score** — composite 0–100 score weighted by anomaly severity, z-scores, and peer confirmation count
- **3D NOC globe** — Three.js WebGL globe with AS node tiers, arc-colored anomaly types, flap ring pulses, fiber cable geodesics, and IP traceroute path overlays
- **Full mobile support** — separate mobile layout with touch globe controls, bottom-sheet AS sidebar, and layer toggle controls

---

## Architecture

16 Docker containers across 5 groups:

| Group | Services |
|-------|----------|
| Storage | timescaledb, neo4j, elasticsearch, redis, minio |
| Collectors | collector-ris, collector-traffic, collector-community, collector-atlas, collector-rpki, collector-topology, collector-baseline |
| Workers | celery-worker, celery-beat |
| App | inet-health-api (:8000), inet-health-frontend (:3000) |

**Backend:** Python 3.12 · FastAPI · Celery + Redis · asyncpg · httpx · spaCy · HuggingFace

**Frontend:** React 18 + TypeScript · Vite · Three.js · D3.js · Socket.io · Zustand · TanStack Query · Tailwind CSS

**Storage:** TimescaleDB (7 hypertables) · Neo4j 5 · Elasticsearch 8 · Redis 7 · MinIO

---

## Data Sources

| Source | Status | Notes |
|--------|--------|-------|
| RIPE RIS Live | ✅ Live | 5 collectors, streaming NDJSON |
| Cloudflare Radar | ✅ Live | 500 hijacks + 500 leaks/cycle (token required) |
| RIPE Atlas | ✅ Live | 4 measurements, ~75K results/cycle |
| RPKI (RIPE validator) | ✅ Live | 500 prefixes/cycle, 4h Redis cache |
| PeeringDB | ✅ Live | 250 IXPs/hour, no auth |
| Reddit | ✅ Live | 6 subreddits, 40 posts/cycle |
| HN Algolia | ✅ Live | No auth |
| Mastodon | ✅ Live | 3 instances, 5 hashtags |
| CAIDA IODA | ⚠️ Partial | Country-level OK; ASN endpoint broken |
| StatusPage feeds | ⚠️ Partial | Cloudflare/GitHub OK; AWS/GCP/Fastly blocked |
| NANOG mailing list | ⛔ Blocked | 403 — low priority |
| X / Twitter | ⛔ Deferred | $100/mo bearer token |

---

## Quickstart

> **For a complete install, configuration, operations, and troubleshooting reference, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).** This Quickstart is the 2-minute version.

### Prerequisites
- Docker Desktop 29.2.1+ (with Compose v5)
- A Cloudflare API token with Radar read access

### 1. Clone and configure
```bash
git clone https://github.com/dwolfenbarger/inet-health-engine.git
cd inet-health-engine
cp .env.example .env
# Edit .env — set CLOUDFLARE_RADAR_TOKEN and database passwords
```

### 2. Start
```bat
start.bat
```
Or directly:
```bash
docker compose up -d
```

### 3. Access
| Interface | URL |
|-----------|-----|
| NOC Globe (frontend) | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Neo4j Browser | http://localhost:7474 |
| MinIO Console | http://localhost:9001 |

### 4. Stop
```bat
stop.bat
```

---

## API

28 REST endpoints + 1 WebSocket. Key endpoints:

```
GET  /api/v1/bgp/summary                   # BGP rates, anomaly counts, top ASNs
GET  /api/v1/bgp/anomalies                 # Detected hijacks, leaks, surges, flaps
GET  /api/v1/intelligence/health-score     # Global internet health score 0–100
GET  /api/v1/intelligence/traceroute       # IP traceroute via Team Cymru + Neo4j
GET  /api/v1/globe/nodes                   # AS nodes for Three.js globe
GET  /api/v1/globe/arcs                    # Anomaly arcs colored by type
GET  /api/v1/events/feed                   # Merged BGP + community events by severity
WS   /ws/events                            # Socket.io live event stream
```

Full inventory at `http://localhost:8000/docs` when running.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values. Required:

| Variable | Purpose |
|----------|---------|
| `CLOUDFLARE_RADAR_TOKEN` | Cloudflare Radar API bearer token |
| `TIMESCALE_PASSWORD` | TimescaleDB password |
| `NEO4J_AUTH` | Neo4j auth (`neo4j/<password>`) |
| `MINIO_PASSWORD` | MinIO admin password |

See `.env.example` for the full list.

---

## Project Structure

```
inet-health-engine/
├── api/                    # FastAPI application
│   ├── api/                # Route modules (bgp, events, globe, intelligence, traffic)
│   ├── main.py
│   └── requirements.txt
├── collectors/             # All data collector services + Celery tasks
│   └── collectors/
│       ├── tasks/          # Celery task definitions per source
│       ├── ripe_ris_collector.py
│       ├── community_collector.py
│       ├── neo4j_topology.py
│       └── ...
├── frontend/               # React 18 + TypeScript NOC UI
│   └── src/
│       ├── components/     # GlobeView, EventRail, EventFeed, ASSidebar, NOCTopBar, ...
│       ├── store/          # Zustand NOC store
│       └── lib/            # AS metadata, cable routes, geo data
├── docker/                 # TimescaleDB init SQL, Redis config, Dockerfiles
├── docker-compose.yml
├── docker-compose-vengeance.yml
├── start.bat
└── stop.bat
```

---

## Known Issues

1. `traffic/regions` and `traffic/outages` endpoints return empty — read path bug in `routes_traffic.py`
2. RPKI Redis cache hit rate ~19–22% — Redis persistence not configured (data lost on restart)
3. ~1 null prefix per 30 min in `withdrawal_surge` — second unsanitised code path in `ripe_ris_collector.py`
4. Neo4j RDAP enrichment incomplete — 14,358 of 14,854 AS nodes still bare `AS{n}` names
5. IRR checking not implemented — hijack confidence relies on RPKI only
6. Elasticsearch `community-signals` index growing unbounded — no ILM policy yet

---

## Host

Developed and deployed on **VENGEANCE** — Windows x86_64, 32-core, 31GB RAM, Docker Desktop 29.2.1.

---

## License

MIT
