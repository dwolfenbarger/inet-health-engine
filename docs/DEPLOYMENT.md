# Deployment & Installation Guide

Complete reference for installing, configuring, and operating the
Internet Health & Status Engine on a Windows host running Docker Desktop.

This guide covers:
- Hardware and software prerequisites
- Pre-install host checks (port reservations, WSL2)
- First-time install
- Configuration via `.env`
- Verification and health checks
- Operational commands
- Architecture (containers, networks, volumes)
- **Complete dependency inventory** (system, Python, Node, external services)
- Troubleshooting (every failure mode seen in production so far)

For an overview of what the engine *does* (BGP analysis, RPKI, globe UI,
APIs), see the project [README](../README.md). This document is purely
about getting it running and keeping it running.

---

## 1. System Requirements

### Hardware

| Resource | Minimum | Recommended | Reference (VENGEANCE) |
|---|---|---|---|
| CPU cores | 8 | 16+ | 32 |
| RAM | 16 GB | 24 GB+ | 31 GB |
| Disk (free) | 60 GB | 200 GB+ SSD | NVMe |
| Network | 25 Mbps sustained | 100 Mbps+ | — |

The collectors are I/O- and network-bound, not CPU-bound. RAM headroom
matters most for Elasticsearch (default 4 GB JVM heap), Neo4j (2 GB heap +
1 GB pagecache), and TimescaleDB shared buffers (2 GB). Leave at least
8 GB free for the host OS and Docker Desktop's own WSL2 VM overhead.

Steady-state ingest writes roughly **1.5–4 million BGP rows per hour**
to TimescaleDB. Plan disk capacity around your retention policy
(default: hypertable compression after 7 days, drop after 30).

### Operating System

Tested on Windows 11 / Windows Server 2022 (x86_64) with Docker Desktop
on the WSL2 backend. Linux hosts work too but the Windows-specific
sections below (WinNAT, WSL2 setup) are not applicable.

---

## 2. Software Prerequisites

| Software | Version | Why |
|---|---|---|
| Docker Desktop | **29.2.1** or newer | Compose v2.5+, WSL2 backend |
| Docker Compose | **v5** (bundled with Docker Desktop ≥ 4.30) | `name:` keyword, `<<:` YAML merge anchors, `depends_on.condition` |
| WSL2 | enabled, kernel ≥ 5.15 | Docker Desktop's Linux runtime |
| PowerShell | 5.1 or 7.x | `start.bat`/`stop.bat` and `setup-vengeance.ps1` |
| Git | 2.40+ | Clone, push |
| Web browser | Modern Chrome / Firefox / Edge | Frontend uses WebGL2 for the globe |

### Optional but recommended

| Software | Why |
|---|---|
| GitHub CLI (`gh`) | One-shot repo create + push |
| `curl` / `httpie` | API verification from host |
| `psql` (PostgreSQL client) | Direct TimescaleDB queries |
| `cypher-shell` (Neo4j) | Topology graph queries |

### Docker Desktop configuration

Inside Docker Desktop → **Settings → Resources**:

- CPUs: **8+** (more is better; collectors and Celery workers are
  parallelism-friendly)
- Memory: **20 GB+**
- Swap: 2 GB
- Disk image size: **128 GB+**
- WSL Integration: enabled for your default distro

Without enough memory allocated to the VM, Elasticsearch will OOM-kill
within minutes of ingest.

---

## 3. Pre-Install Host Checks

Two Windows-specific gotchas have bitten us in production. Run these
checks *before* the first install — they take 30 seconds and save hours.

### 3.1 Check the WinNAT excluded port range

Windows reserves blocks of TCP ports at boot for Hyper-V/WSL2 dynamic
port forwarding. The reserved ranges are random — they change on every
reboot. If a port the engine needs falls inside a reservation, Docker
will fail with:

```
bind: An attempt was made to access a socket in a way forbidden by its
access permissions.
```

The ports the engine binds on the host are: **3000, 5432, 6379, 7474,
7687, 8000, 19000, 19001, 19200**. Verify all are bindable:

```powershell
$ports = 3000,5432,6379,7474,7687,8000,19000,19001,19200
foreach ($p in $ports) {
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any,$p)
        $l.Start(); $l.Stop()
        Write-Host "$p OK" -ForegroundColor Green
    } catch {
        Write-Host "$p BLOCKED (WinNAT reservation)" -ForegroundColor Red
    }
}
```

If any port reports `BLOCKED`, edit `docker-compose.yml` and remap the
**host side** of the affected `ports:` entry to a free port. The
container side stays the same so internal Docker DNS keeps working.
For example, if 8000 is reserved, change `"8000:8000"` to `"18000:8000"`
and access the API at `http://localhost:18000`.

The `9200` (ES), `9000` and `9001` (MinIO) ports were originally on the
reservation list and are already remapped to `19200`, `19000`, and
`19001` respectively in the shipped compose file.

### 3.2 Verify WSL2 backend

```powershell
wsl --status        # should show "Default Version: 2"
wsl -l -v           # at least one distro must be VERSION 2
docker version      # Server: must show "OS/Arch: linux/amd64"
```

If Docker Desktop reports the Hyper-V backend, switch to WSL2 in
Settings → General. Performance, volume mounting, and our
container-IP semantics all assume WSL2.

### 3.3 Confirm Docker Desktop is reachable

```powershell
docker version
docker compose version
```

If `docker version` hangs, Docker Desktop's daemon isn't running.
Open the Docker Desktop UI and wait for the whale to stop animating.

---

## 4. Installation

### 4.1 One-shot quick-start (recommended)

```powershell
git clone https://github.com/dwolfenbarger/inet-health-engine.git C:\ai\inet-health-engine
cd C:\ai\inet-health-engine
Copy-Item .env.example .env
# Edit .env — set CLOUDFLARE_RADAR_TOKEN and rotate the changeme_* passwords
docker compose up -d
```

First-run timing on VENGEANCE-class hardware:

| Phase | Time | What's happening |
|---|---|---|
| Image pulls (storage tier) | 3–5 min | TimescaleDB, Neo4j, ES 8, Redis 7, MinIO |
| Image builds (api, collectors, frontend) | 6–10 min | pip install pybgpstream + spaCy = the slowest layer |
| Container start + healthchecks | 60–90 s | ES needs ~30 s to go healthy |
| First BGP data visible in API | 90 s after ES healthy | RIS Live streaming begins immediately |
| Globe renders nodes/arcs | ~2 min | After Neo4j topology cycle and first anomaly batch |

### 4.2 Manual install (step-by-step)

If you want visibility into each phase:

```powershell
cd C:\ai\inet-health-engine
Copy-Item .env.example .env
# Fill in .env values (see §5)

# 1. Validate the compose file syntax
docker compose config --quiet
# (no output = OK; errors print to stderr)

# 2. Pull the upstream storage images in parallel
docker compose pull timescaledb neo4j elasticsearch redis minio

# 3. Build the application images in parallel
docker compose build --parallel

# 4. Bring up the storage tier first
docker compose up -d timescaledb neo4j elasticsearch redis minio

# 5. Wait for all storage healthchecks to pass
docker compose ps
# repeat until all five show (healthy); usually 60–90 s

# 6. Bring up the rest
docker compose up -d
```

### 4.3 The two compose files — which to use

The repository contains **two** Compose files:

| File | Use case | Volumes | Code mounts |
|---|---|---|---|
| `docker-compose.yml` | **Production / canonical**. Default for `docker compose` commands. | Named volumes (Docker-managed) | Code baked into images |
| `docker-compose-vengeance.yml` | **Dev mode**. Hot-reload for `api/` and `collectors/`. | Bind mounts to `./data/*` | `./api`, `./collectors` mounted into containers |

Use the canonical file unless you're actively editing collector or
API source. `docker-compose-vengeance.yml` keeps `--reload` on uvicorn
and bind-mounts code so edits take effect on the next request.

The legacy `setup-vengeance.ps1` script copies the dev-mode file over
the canonical one. **Do not run it** unless you specifically want
dev mode — and if you do, run it once and don't re-run it after
editing `docker-compose.yml`, because it will overwrite your edits.

---

## 5. Configuration — `.env`

Copy `.env.example` to `.env` and edit. Never commit `.env` (it's in
`.gitignore` already).

### 5.1 Required

| Variable | Default | Notes |
|---|---|---|
| `TIMESCALE_DB` | `inethealth` | DB name; safe to keep |
| `TIMESCALE_USER` | `inetuser` | App role; safe to keep |
| `TIMESCALE_PASSWORD` | `changeme_timescale` | **Rotate before exposing port 5432** |
| `NEO4J_AUTH` | `neo4j/changeme_neo4j` | Format: `user/password`. **Rotate.** |
| `MINIO_USER` | `minioadmin` | **Rotate before exposing 19000/19001** |
| `MINIO_PASSWORD` | `changeme_minio` | **Rotate.** |

### 5.2 Optional (data sources)

| Variable | Required for | Get one at |
|---|---|---|
| `CLOUDFLARE_RADAR_TOKEN` | Cloudflare Radar collector (route leaks + hijacks) | https://dash.cloudflare.com/profile/api-tokens — needs `Account.Account Analytics: Read` |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | Authenticated Reddit ingest (higher rate limits) | https://www.reddit.com/prefs/apps — script-type app. **Optional** — the collector falls back to `.json` endpoints with no auth |
| `X_BEARER_TOKEN` | X/Twitter signal collector | $100/mo Basic tier. Currently **deferred** in code |

If a data source token is unset the collector logs a warning and
proceeds in degraded mode. Nothing crashes.

### 5.3 Tuning (have sane defaults)

| Variable | Default | Effect |
|---|---|---|
| `POLL_INTERVAL` | `300` | Community/traffic poll cadence (seconds) |
| `BGP_WINDOW_SECONDS` | `60` | RIS Live aggregation window |
| `RIPE_RIS_COLLECTORS` | `rrc00,rrc01,rrc03,rrc04,rrc05` | RIS collector node subscription set |

---

## 6. Verification

After `docker compose up -d` settles, run these checks. Each is a
one-liner.

### 6.1 All 16 containers running

```powershell
docker compose ps -a --format "table {{.Name}}`t{{.State}}`t{{.Status}}"
```

Expected: all 16 in `running` state. The four storage backends
(`timescaledb`, `neo4j`, `redis`, `elasticsearch`, `minio`) should
additionally show `(healthy)` within 90 seconds of start.

### 6.2 Network membership

```powershell
docker network inspect inet-health-engine_inet-health `
  --format "{{range .Containers}}{{.Name}} -> {{.IPv4Address}}`n{{end}}" | Sort-Object
```

Expected: all 16 listed. Static IPs (must match these exactly):

| Service | IP |
|---|---|
| timescaledb | `172.30.0.10` |
| neo4j       | `172.30.0.11` |
| elasticsearch | `172.30.0.12` |
| redis       | `172.30.0.13` |
| minio       | `172.30.0.14` |
| api         | `172.30.0.20` |
| frontend    | `172.30.0.30` |

If any of these statics is held by a *collector*, see §10.2 (IPAM
collision recovery). The 9 dynamic services land on `.2` through `.9`
plus `.15` (collector-baseline post-recovery).

### 6.3 Endpoint smoke tests

```powershell
# API up?
Invoke-WebRequest http://localhost:8000/health  -UseBasicParsing | % StatusCode  # 200

# Frontend up?
Invoke-WebRequest http://localhost:3000/        -UseBasicParsing | % StatusCode  # 200

# BGP ingest live?
(Invoke-RestMethod http://localhost:8000/api/v1/bgp/summary).bgp_rate_per_min
# expect a number > 1000 within ~2 min of start

# Health score computed?
(Invoke-RestMethod http://localhost:8000/api/v1/intelligence/health-score).score
# expect 0-100

# Storage backend reachable from API?
Invoke-RestMethod http://localhost:8000/api/v1/status
# expect {timescale: ok, redis: ok, elasticsearch: ok, neo4j: ok}
```

### 6.4 Direct storage access (debug only)

| Service | URL | Auth |
|---|---|---|
| TimescaleDB | `psql -h localhost -p 5432 -U inetuser inethealth` | `$TIMESCALE_PASSWORD` |
| Neo4j Browser | http://localhost:7474 | `$NEO4J_AUTH` |
| Elasticsearch | http://localhost:19200/_cluster/health | none (xpack.security disabled) |
| Redis | `redis-cli -h localhost -p 6379` | none |
| MinIO Console | http://localhost:19001 | `$MINIO_USER` / `$MINIO_PASSWORD` |

These are exposed for local debugging only. In a real deployment
remove the host-port mappings for everything except api (8000) and
frontend (3000), and put a reverse proxy in front.

---

## 7. Operations

### Daily

```powershell
# Status
docker compose ps

# Tail one service's logs
docker compose logs -f --tail=100 collector-ris

# Tail everything (firehose; usually too much)
docker compose logs -f

# Restart one container without disturbing others
docker compose restart api

# Recreate one container (use after compose-file edits)
docker compose up -d --force-recreate --no-deps api

# Stop everything but keep volumes
docker compose down

# Start (after `down`)
docker compose up -d

# Stop everything AND wipe data (destroys named volumes)
docker compose down --volumes
```

The bundled `start.bat` and `stop.bat` are convenience wrappers that
just call `docker compose up -d` and `docker compose down` from the
project root.

### Resource usage

```powershell
docker stats --no-stream
```

At steady state on VENGEANCE the heaviest containers are:

| Container | CPU | RAM |
|---|---|---|
| elasticsearch | 5–15 % | ~3.5 GB |
| collector-ris | 5–10 % | ~700 MB |
| neo4j | 1–3 % | ~2.5 GB |
| timescaledb | 2–8 % | ~2 GB |
| api | 1–2 % | ~400 MB |
| frontend (nginx) | <1 % | ~10 MB |

---

## 8. Architecture

### 8.1 Container map

```
                               ┌──────────────────┐
                               │    frontend      │  3000:80
                               │  React + nginx   │
                               └────────┬─────────┘
                                        │ /api/* /ws/*
                                        ▼
                ┌──────────────────────────────────────────┐
                │              inet-health-api             │  8000:8000
                │       FastAPI · uvicorn 4 workers        │
                └──┬──────────┬──────────┬──────────┬──────┘
                   │          │          │          │
                ┌──▼──┐  ┌────▼────┐ ┌───▼────┐ ┌───▼──────┐
                │redis│  │timescale│ │ neo4j  │ │   ES     │
                │  6379│  │  5432   │ │7474/7687│ │ 19200:9200│
                └──▲──┘  └────▲────┘ └────▲───┘ └──▲───────┘
                   │          │           │        │
        ┌──────────┴──────────┴───────────┴────────┴──────────┐
        │                Collector tier (7)                    │
        │  ris · traffic · community · atlas · rpki ·          │
        │  topology · baseline                                  │
        └───────────────────────────┬───────────────────────────┘
                                    │
                          ┌─────────┴────────┐
                          │  Celery (worker  │ ── redis broker
                          │   + beat)        │
                          └──────────────────┘
                                    │
                                    ▼
                          ┌──────────────────┐
                          │      MinIO       │ 19000:9000, 19001:9001
                          │   (MRT dumps)    │
                          └──────────────────┘
```

### 8.2 Bridge network

Single user-defined bridge: `inet-health-engine_inet-health`,
subnet `172.30.0.0/24`. All 16 containers attach. Internal DNS
resolves service names (e.g. `redis`, `elasticsearch:9200`). The
host port mappings are only for external/debug access; container-
to-container traffic uses the internal addresses.

### 8.3 Persistent volumes

The canonical compose file uses **named volumes** (Docker-managed).
On Windows + WSL2 these live under `\\wsl$\docker-desktop-data\data\docker\volumes\`.

| Volume | Service | What's in it |
|---|---|---|
| `inet-health-engine_timescale-data` | timescaledb | All PostgreSQL data including 7 hypertables |
| `inet-health-engine_neo4j-data`     | neo4j | AS topology graph |
| `inet-health-engine_es-data`        | elasticsearch | Community signals + events index |
| `inet-health-engine_redis-data`     | redis | RPKI cache, BGP rolling window, pubsub state |
| `inet-health-engine_minio-data`     | minio | Raw MRT dumps |

Inspect with:

```powershell
docker volume ls --filter "name=inet-health-engine"
docker volume inspect inet-health-engine_timescale-data
```

`docker compose down --volumes` deletes all five — full reset.
`docker compose down` alone preserves them.

### 8.4 Host port map

| Host port | Container | Container port | Purpose |
|---|---|---|---|
| 3000  | frontend     | 80   | React app |
| 8000  | api          | 8000 | FastAPI + WebSocket |
| 5432  | timescaledb  | 5432 | TSDB direct access |
| 7474  | neo4j        | 7474 | Neo4j Browser |
| 7687  | neo4j        | 7687 | Bolt protocol |
| 6379  | redis        | 6379 | Redis CLI |
| 19200 | elasticsearch| 9200 | ES HTTP (remapped from 9200 — WinNAT reservation) |
| 19000 | minio        | 9000 | MinIO S3 API (remapped from 9000) |
| 19001 | minio        | 9001 | MinIO console (remapped from 9001) |

Container-internal ports (the right side) are what the application
code uses. **Never** change the right side without updating env
vars and code.

---

## 9. Complete Dependency Inventory

### 9.1 External services (no install — accessed over the public internet)

| Source | Endpoint(s) | Auth | Used by |
|---|---|---|---|
| RIPE RIS Live | `wss://ris-live.ripe.net/v1/ws/` | none | collector-ris |
| RIPE Stat | `https://stat.ripe.net/data/bgp-state` | none | collector-ris (enrichment) |
| Cloudflare Radar | `https://api.cloudflare.com/client/v4/radar` | bearer token | collector-traffic |
| RIPE Atlas | `https://atlas.ripe.net/api/v2/measurements/{id}/results` | none | collector-atlas |
| RIPE RPKI Validator | `https://rpki-validator.ripe.net/api/v1` | none | collector-rpki |
| PeeringDB | `https://peeringdb.com/api` | none | collector-topology |
| CAIDA AS-Rel | `https://publicdata.caida.org/datasets/as-relationships/` | none | collector-topology |
| ARIN RDAP | `https://rdap.arin.net/registry` | none | collector-topology, api |
| RIPE RDAP | `https://rdap.db.ripe.net` | none | collector-topology, api |
| Team Cymru DNS | `*.cymru.com` (whois-over-DNS) | none | api (traceroute) |
| Reddit `.json` | `https://www.reddit.com/r/{sub}/new.json` | optional OAuth | collector-community |
| Hacker News (Algolia) | `https://hn.algolia.com/api/v1` | none | collector-community |
| Mastodon | `mastodon.social`, `fosstodon.org`, `hachyderm.io` (each `/api/v1/timelines/tag/...`) | none | collector-community |
| Cloudflare StatusPage | `https://www.cloudflarestatus.com/api/v2/incidents.json` | none | collector-community |
| GitHub StatusPage | `https://www.githubstatus.com/api/v2/incidents.json` | none | collector-community |

The system requires **outbound HTTPS to all of the above** plus
**WSS to ris-live.ripe.net**. No inbound exposure is needed for
data collection.

### 9.2 Container base images (pulled from Docker Hub / Elastic Registry)

| Image | Tag | Purpose |
|---|---|---|
| `python` | `3.12-slim` | Base for `api`, `collectors`, `celery-*` |
| `node` | `22-slim` | Frontend build stage |
| `nginx` | `alpine` | Frontend serve stage |
| `timescale/timescaledb` | `latest-pg16` | TimescaleDB on PostgreSQL 16 |
| `neo4j` | `5-community` | Graph DB (with APOC plugin) |
| `docker.elastic.co/elasticsearch/elasticsearch` | `8.13.4` | Elasticsearch |
| `redis` | `7-alpine` | Cache + Celery broker |
| `minio/minio` | `latest` | S3-compatible object storage |

### 9.3 Debian apt packages installed inside containers

**`api/Dockerfile`** (Python 3.12-slim base):
- `curl` (for healthchecks and debug)

**`collectors/Dockerfile`** (Python 3.12-slim base):
- `build-essential` — gcc/make for compiling C extensions
- `libpcap-dev`, `libpcap0.8` — for `pybgpstream`
- `libssl-dev`, `libffi-dev` — for cryptography wheels
- `libxml2-dev`, `libxslt1-dev`, `zlib1g-dev` — for `lxml`
- `pkg-config`, `git`, `curl`

**`frontend/Dockerfile`** (multi-stage):
- Build stage: `node:22-slim` (Debian Bookworm) — no extra apt packages
- Serve stage: `nginx:alpine` — no extra apk packages

### 9.4 Python packages — `api/requirements.txt`

```
fastapi==0.111.0
uvicorn[standard]==0.30.1
websockets==12.0
python-socketio==5.11.3
asyncpg==0.29.0
redis==5.0.7
elasticsearch[async]==8.13.2
aiohttp==3.9.5
pydantic==2.8.0
pydantic-settings==2.3.4
python-dotenv==1.0.1
structlog==24.2.0
httpx==0.27.0
netaddr==1.3.0
tenacity==8.4.2
```

### 9.5 Python packages — `collectors/requirements.txt`

```
# BGP
pybgpstream==2.0.0

# Async / HTTP
httpx==0.27.0
aiohttp==3.9.5

# Task queue
celery[redis]==5.4.0
redis==5.0.7

# Database clients
asyncpg==0.29.0
psycopg2-binary==2.9.9
neo4j==5.22.0
elasticsearch[async]==8.13.2

# Object storage
minio==7.2.8

# Scraping
beautifulsoup4==4.12.3
lxml==5.2.2

# Reddit
praw==7.7.1

# Data / utils
pydantic==2.8.0
pydantic-settings==2.3.4
python-dotenv==1.0.1
structlog==24.2.0
tenacity==8.4.2
pytz==2024.1
netaddr==1.3.0
```

`spacy` and `transformers` are reserved (commented out in
requirements) for Phase 3 NLP and not yet installed.

`pybgpstream` is the heaviest build — it pulls a C library and may
fail on ARM hosts; the Dockerfile catches the failure and the
collectors run in stub mode. On x86_64 (VENGEANCE) it builds clean.

### 9.6 Frontend npm packages — `frontend/package.json`

**Runtime:**
```
react              ^19.2.5
react-dom          ^19.2.5
zustand            ^5.0.12       (state management)
@tanstack/react-query  ^5.100.5  (server-state cache)
socket.io-client   ^4.8.3        (WebSocket events)
three              ^0.184.0      (3D globe)
globe.gl           ^2.45.3       (Three.js wrapper)
d3                 ^7.9.0        (event rail / charts)
lucide-react       ^1.11.0       (icons)
tailwindcss        ^4.2.4        (styling)
@tailwindcss/vite  ^4.2.4
```

**Dev/build:**
```
vite               ^8.0.10
@vitejs/plugin-react  ^6.0.1
typescript         ~6.0.2
eslint             ^10.2.1
typescript-eslint  ^8.58.2
@types/{react,react-dom,three,d3,node}
```

The frontend builds in the multi-stage Dockerfile and ships as
static files behind nginx — runtime image has zero Node dependencies.

---

## 10. Troubleshooting

Every failure mode listed here has actually happened in production
on VENGEANCE. The recovery steps are field-tested.

### 10.1 Frontend stuck restarting — `host not found in upstream "api"`

**Symptom:** `inet-health-frontend` cycles every 7–30 seconds. Logs
show `[emerg] host not found in upstream "api" in /etc/nginx/conf.d/default.conf`.

**Root cause:** Either (a) the api container isn't running, or
(b) the api container is running but **detached from the bridge
network** (NetworkMode set, but `NetworkSettings.Networks={}`).
Detachment happens when a container is started out-of-band while
its network is in an unusual state.

**Diagnose:**
```powershell
docker inspect inet-health-api --format '{{json .NetworkSettings.Networks}}'
```
- `{}` = detached (case b)
- A network object = case a (something else is wrong; check api logs)

**Fix:**
```powershell
docker compose up -d --force-recreate --no-deps api
docker compose up -d --force-recreate --no-deps frontend
```

### 10.2 MinIO won't start — `Address already in use` (network setup)

**Symptom:** `failed to set up container networking: Address already in use`.
Note: this is a **network-layer** error, not a host-port bind error.

**Root cause:** MinIO has a static IP `172.30.0.14` declared in compose,
but a collector with no static decl was assigned `.14` dynamically while
MinIO was down. IPAM hands out the lowest-free address; if MinIO isn't
in the network, `.14` looks free.

**Diagnose:**
```powershell
docker network inspect inet-health-engine_inet-health `
  --format "{{range .Containers}}{{.Name}} -> {{.IPv4Address}}`n{{end}}" |
  Select-String "172.30.0.14"
```
If the output names a collector instead of `inet-health-engine-minio-1`,
that's the collision.

**Fix (force the collector off `.14`, then start MinIO):**
```powershell
docker rm -f <colliding-collector-name>     # e.g. collector-baseline
docker compose up -d minio                   # claims .14
docker compose up -d <colliding-collector-name>  # gets next free, .15
```

`docker restart` and `docker compose up -d --force-recreate` are
*not* sufficient — the IP assignment persists across both.

### 10.3 Any service fails with "bind: forbidden"

**Symptom:** `bind: An attempt was made to access a socket in a way
forbidden by its access permissions`.

**Root cause:** The host port mapped in compose has been pulled into
a Windows WinNAT excluded port range. Reservations are random and
change on every Windows reboot.

**Fix:** Run the port test from §3.1 to find a free host port, edit
`docker-compose.yml` (and `docker-compose-vengeance.yml` for symmetry)
to remap the host side only. Restart:
```powershell
docker compose up -d <service>
```

The shipped compose files already remap `9200`, `9000`, and `9001`
to `19200`, `19000`, `19001`. If you reboot Windows and one of those
*new* ports lands inside a reservation, you'll need to remap again.
The `19xxx` range is empirically less collision-prone than `9xxx`.

### 10.4 Containers exited with code 137

**Symptom:** A container shows `Exited (137)` in `docker compose ps -a`.

**Root cause:** Two possibilities; check the container's logs to
distinguish.

| Log signature | Meaning | Action |
|---|---|---|
| Graceful `stopping ...` / `Warm shutdown (MainProcess)` near the tail | SIGKILL after Docker's stop-timeout drained — a `docker compose down` or host-shutdown event ran out of time | Just bring it back up; data is fine |
| Last lines mention OOM, killed, or memory pressure | Actual OOM kill | Increase Docker Desktop memory allocation (Settings → Resources). For ES specifically, lower the `ES_JAVA_OPTS=-Xmx` from 4g to 2g |

We hit case 1 on 2026-05-04 — host shutdown left celery-worker
and elasticsearch with 137 exits but no actual memory pressure.

### 10.5 Stale-shutdown recovery (full reset)

If multiple services are exited from a multi-day downtime, the
cleanest single-command recovery:

```powershell
cd C:\ai\inet-health-engine
docker compose up -d
```

Compose will:
- Leave already-running, correctly-attached containers alone
- Recreate any that are detached from the network
- Start any that are exited (provided they have `restart:
  unless-stopped`, which all 16 do)

If MinIO collides on `.14`, apply §10.2 then re-run `up -d`.

### 10.6 Diagnostic snapshot (always-useful one-liner)

Paste-friendly status dump for filing issues or debugging:

```powershell
cd C:\ai\inet-health-engine
docker compose ps -a
docker network inspect inet-health-engine_inet-health `
  --format "{{range .Containers}}{{.Name}} -> {{.IPv4Address}}`n{{end}}"
docker stats --no-stream
```

---

## 11. Backup & Disaster Recovery

### 11.1 What's worth backing up

| Volume | Worth backup? | Why |
|---|---|---|
| `timescale-data` | **Yes** | Historical BGP rows, anomalies, atlas measurements — months of research data |
| `neo4j-data` | **Yes** | AS topology graph + RDAP enrichments — expensive to rebuild |
| `es-data` | Optional | Community signals — re-fetchable from upstream |
| `redis-data` | No | Cache and rolling window — rebuilds in minutes |
| `minio-data` | Depends | Only if you've enabled MRT dump archiving |

### 11.2 Online backup (no downtime)

```powershell
# TimescaleDB → SQL dump
docker exec inet-health-engine-timescaledb-1 `
    pg_dump -U inetuser -d inethealth -Fc -f /tmp/inethealth.dump
docker cp inet-health-engine-timescaledb-1:/tmp/inethealth.dump `
    .\backups\inethealth-$(Get-Date -Format yyyyMMdd).dump

# Neo4j → online backup (community edition uses dump)
docker exec inet-health-engine-neo4j-1 neo4j-admin database dump `
    --to-path=/data/backups neo4j
```

### 11.3 Cold backup (with downtime)

```powershell
docker compose stop
docker run --rm -v inet-health-engine_timescale-data:/data `
    -v ${PWD}/backups:/backup alpine `
    tar czf /backup/timescale-$(Get-Date -Format yyyyMMdd).tar.gz -C /data .
# repeat per volume
docker compose start
```

---

## 12. Upgrade Path

### 12.1 Application code

```powershell
cd C:\ai\inet-health-engine
git pull
docker compose build --parallel
docker compose up -d
```

Compose detects rebuilt images and recreates only affected
containers. Storage tier is untouched.

### 12.2 Storage tier image bumps

Bumping a storage image (e.g. neo4j 5 → 6, ES 8 → 9) is **not**
a routine `docker compose up`. Read upstream migration notes,
take a backup (§11.2), then:

```powershell
# Pin the new tag in docker-compose.yml first, then:
docker compose pull <service>
docker compose up -d <service>
```

For Elasticsearch major-version jumps, expect index rebuild time.
For Neo4j majors, run `neo4j-admin database migrate` in a one-off
container before bringing the service up against the upgraded image.

---

## 13. Security Notes (production hardening)

The default deployment is **lab-grade** — convenient for development
on a trusted machine, not production-safe as shipped:

- All five storage backends bind to host ports. Anyone reachable
  on the host can connect.
- ES `xpack.security.enabled=false` and Neo4j auth uses a default
  password from `.env`.
- The api and frontend are HTTP only, no TLS.

For production:
1. Remove host port mappings for `timescaledb`, `neo4j`, `redis`,
   `elasticsearch`, `minio`. Internal Docker DNS is sufficient.
2. Put a reverse proxy (Caddy, Traefik, nginx) in front of `api`
   and `frontend` with TLS and authentication.
3. Rotate every `changeme_*` value in `.env`.
4. Enable ES security and create a non-root role for the api/collectors.
5. Set `--read-only` on app containers or use a non-root user
   (Dockerfiles already create `apiuser` / `collector` UID 1001).

---

## 14. Reference

### 14.1 File layout

```
inet-health-engine/
├── api/                          # FastAPI service
│   ├── Dockerfile                # python:3.12-slim base
│   ├── main.py
│   ├── requirements.txt
│   └── api/                      # Route modules
├── collectors/                   # 7 collectors + Celery worker/beat
│   ├── Dockerfile                # python:3.12-slim + bgpstream build deps
│   ├── requirements.txt
│   └── collectors/               # Module source
├── frontend/                     # React 18 + TypeScript
│   ├── Dockerfile                # node:22-slim build → nginx:alpine
│   ├── package.json
│   ├── nginx.conf                # SPA + /api and /ws upstream → api:8000
│   └── src/
├── docker/
│   ├── Dockerfile.timescale      # timescale + init.sql baked in
│   ├── timescale-init.sql        # Schema + hypertables + indexes
│   └── redis.conf                # Redis config (used by -vengeance.yml only)
├── docs/
│   └── DEPLOYMENT.md             # ← you are here
├── .env.example                  # Template; copy to .env
├── docker-compose.yml            # Canonical (production-ish, named volumes)
├── docker-compose-vengeance.yml  # Dev-mode (bind mounts, hot reload)
├── start.bat                     # = docker compose up -d
├── stop.bat                      # = docker compose down
├── setup-vengeance.ps1           # First-run helper (use with care, see §4.3)
├── README.md                     # Project overview
└── SYSTEM_PROMPT.md              # Engineering session bootstrap (internal)
```

### 14.2 Useful upstream docs

- **Docker Desktop on Windows:** https://docs.docker.com/desktop/install/windows-install/
- **WSL2 setup:** https://learn.microsoft.com/en-us/windows/wsl/install
- **TimescaleDB:** https://docs.timescale.com/
- **Neo4j 5:** https://neo4j.com/docs/operations-manual/5/
- **Elasticsearch 8.13:** https://www.elastic.co/guide/en/elasticsearch/reference/8.13/index.html
- **RIPE RIS Live:** https://ris-live.ripe.net/
- **Cloudflare Radar API:** https://developers.cloudflare.com/radar/

---

*Document generated 2026-05-08. Update on material changes to compose
files, dependencies, or recovery procedures.*
