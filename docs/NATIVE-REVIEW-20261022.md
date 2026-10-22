# Native deployment review — 2026-10-22

Reviewed by OpenAI at David's request. Findings distinguish observed runtime from README claims.

## Purpose

Internet-wide routing intelligence for network engineers and service-provider planners: BGP anomaly detection, RPKI authorization checks, AS/IXP relationships, connectivity measurements and independent outage/community evidence, presented through a 3D NOC globe and investigation panels. Detection signals are leads to investigate; an origin change alone does not establish a hijack.

## Verified environment

| Lane | Guest | Address recorded at provisioning | State observed |
| --- | --- | --- | --- |
| PROD | LXC553, inet-health-prod | 192.168.1.113 | Running native application and five backing stores |
| UAT | LXC554, inet-health-uat | 192.168.1.75 | Running; no failed systemd units; full parity not established |
| DEV1 | LXC555, inet-health-dev1 | 192.168.1.65 | Running API/Celery/nginx/PostgreSQL/Redis; missing collector dependency tenacity and git executable |

Host: pve2, node proxmox1, SSH 192.168.1.20. PROD allocation: 8 cores, 16GiB RAM, 200GiB disk; disk ~4% used. Source and runtime venv: /home/david/inet-health-engine and .venv. API working directory: source/api. Native units: inet-api, inet-celery-worker, inet-celery-beat, nginx, postgresql@17-main, redis-server, neo4j, elasticsearch, minio. Nginx serves frontend/dist and proxies /api/ and /ws/ to localhost:8000.

The Vengeance project ledger inet-health-engine already existed, with three provisioning logs but blank state sections. No duplicate ledger was created. The old SYSTEM_PROMPT.md describes a Valley Pi/Docker environment and the README describes Windows/Docker; neither describes this deployment.

## Code preservation

GitHub master was c3d990aadb1f694d7e36101f085ff0dda2b85a8a. PROD's clean tracked tree was bd87a80acdd10ee05c93d09d1126530e048f58a0, one unpublished commit ahead: detector, peers/watchlist API and globe/UI changes across 18 files. Preserved unchanged as recovery/prod-baseline-20261022. Its database additions exist on PROD but are not represented as versioned schema migrations in that commit. The existing Windows checkout has an unrelated docker-compose.yml modification and was left untouched.

## Findings and proposed sequence

1. **P1 — stalled ingest:** API reported zero last-hour BGP updates, last update 2026-10-22T13:19:09.720Z. Celery logs show repeated Event loop is closed during batch writes and updates_written total=0. Task wrappers call asyncio.run repeatedly inside reused prefork children while modules retain loop-bound PostgreSQL/Redis clients. This branch replaces those task calls with one asyncio.Runner per child, isolates contextvars per invocation, and closes clients on child shutdown. Standalone collector entry points retain their own lifetime.
2. **P1 — misleading health:** /health is only a static liveness response. /api/v1/status reports score100 and healthy source labels despite stale BGP input. Add separate dependency readiness and source freshness/coverage, suppress confident scores when evidence is missing, and verify failure/staleness transitions.
3. **P1 — deployment provenance/parity:** preserve and review the recovered assessment commit; add schema migrations, native install/service configuration and a reproducible dependency set. DEV1 currently has newer package versions than repository pins; native Python is3.13 versus documented3.12. Verify all lane dependency/schema parity before promotion.
4. **P1 — access posture:** PROD listens on all interfaces for8000/9200/9000/9001. Scope of network reachability was not independently tested. Review binding/firewall/authentication before external publication, especially the mutating watchlist API. No firewall, credentials or bindings were changed here.
5. **P2 — data correctness:** traffic region/outage APIs returned empty; determine whether this is missing ingestion, classification or a read-path defect. Review unsupported high-confidence origin-change detection, duplicate incident generation, missing versioned migrations, and unbounded watchlist/incident query inputs in the recovered commit.
6. **P2 — operator usefulness:** establish data retention/capacity, measurable collector freshness, rate limits and source coverage; then validate desktop/mobile globe behavior and investigation workflows with real evidence. The reported LXC load average is not sufficient to attribute CPU saturation; measure cgroup/host CPU separately.

## Validation of this branch

- Six unittest regressions pass on Windows Python3.14 and DEV1 Linux Python3.13: cross-loop failure reproducer, repeated loop-bound client reuse, retry after exception, context isolation, cleanup and PID ownership.
- Read-only DEV1 integration: existing fresh-loop pattern passes cycle1 then fails cycle2 with asyncpg InterfaceError/closed connection. Persistent-loop candidate passes three consecutive SELECT1 + Redis PING cycles and clean pool shutdown. No production database data was modified.
- DEV1 lacks tenacity; the pinned8.4.2 wheel was installed only to /tmp/inet-review-20261022/test-deps for the isolated test process. Existing venv/services were not modified.
- compileall and git diff --check pass. This does not yet prove recovery under full live collection or every external-source failure condition.
- Candidate source staged under /tmp/inet-review-20261022 on DEV1 only. No live service restart, runtime source replacement, migration, merge or production deployment occurred.

## Next execution gate

Complete native DEV1 dependencies/parity, run sequential real collection cycles and confirm advancing database timestamps plus absence of swallowed batch-write errors. Add honest readiness/freshness reporting, then validate UAT. Production promotion requires a reviewed candidate and a worker-drain gate; do not restart a worker while it holds live collection work.

## Reproduce tests

Run python -m unittest discover -s tests -v from the repository. For the optional read-only integration comparison, provide mode(old or new), candidate root and the private runtime env-file path to tests/inet_integration.py. Run old and new in separate processes; old is expected to fail on its second invocation. Never print or commit runtime credentials.
