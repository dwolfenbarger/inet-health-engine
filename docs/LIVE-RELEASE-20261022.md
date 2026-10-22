# Live routing release — 22 October 2026

David explicitly authorized production deployment with live data. The new NOC interface is installed at <http://192.168.1.113/> on `inet-health-prod` (LXC 553). UAT is <http://192.168.1.75/>. This is a first live routing release, with partial observation coverage; the wider global NOC roadmap remains in progress.

## Delivered behavior

- Overview, origin-ASN search, exact IPv4/IPv6 CIDR investigation, and comparison of observed updates for one prefix across collector/peer viewpoints.
- URL state retains workspace, resource, family, collector, time window, visualization and comparison viewpoints. Keyboard skip navigation preserves that state.
- 2D/rotatable 3D views plot actual RIS collector cities. Topology shows a selected observed AS sequence, retaining repeated ASNs. Geography does not imply ASN location or a measured forwarding path.
- Read-only, parameterized `/api/v1/noc/snapshot` queries show explicit fresh/stale/unavailable states, UTC event times, bounded samples, raw route JSON and heuristic anomaly candidates. No global health score is inferred. Failed API reads retain visibly unavailable/stale evidence rather than creating an all-clear.
- Collector workers reuse one event loop per process. Write failures propagate. Synthetic BGP fallback and synthetic community fallback paths are disabled. RIPEstat no longer boosts confidence using the same underlying RIS observations.
- Flaps count announce/withdraw transitions per collector/peer/prefix, with bounded state. New anomaly records retain source and peer evidence. Historical records without attribution are excluded from collector-filtered results.
- Single next-hop attributes use the actual announcement value, rather than the observer's address. Multi-address IPv6 attributes remain lossless in `next_hop_raw`; `next_hop` is null when the attribute cannot fit one INET address. Apply `docker/migrations/20261022-next-hop-raw.sql` before updating the API/collector. This additive field is safe to retain on rollback.

## Exact deployment

Application/collector source: `4635b7163341ce01a0309c1d271399a7dd078d96` on `openai/live-noc-production`. It incorporates the preserved production baseline `bd87a80`, design branch, and collector-loop fix. Later documentation commits do not change the installed application source.

| Artifact | SHA-256 |
| --- | --- |
| Source archive | `66d5883db8bfe9f5d9a9b36fd05b0ec1710f309a84cf9c3092beeba389c8d5e3` |
| Frontend archive | `aa257e78fcea0fe80feba1f525cf8094e39b84bac2b543b64eefa226b7f35c4d` |
| Git bundle | `82af591276784eecf06d023af4ae6437f96ac10c6bee2e24bf6cc8e721b30567` |

Native services remain `inet-api`, `inet-celery-worker`, `inet-celery-beat`, and nginx. Source/venv remain `/home/david/inet-health-engine`. A service drop-in reads `/etc/inet-health/live.env` with `BGP_WINDOW_SECONDS=60`, overriding the old 300-second window. The RIS schedule remains 90 seconds; collection stops at 50,000 updates to bound memory and processing. This samples the feed and has collection gaps. The UI queries at most the latest 20,000 matching updates and presents at most 200 viewpoint/prefix rows and 100 anomaly candidates, refreshing every 20 seconds.

Configured collectors: `rrc00`, `rrc11`, `rrc14`, `rrc16`, `rrc21`. Approximate city locations come from the [RIPE collector documentation](https://ris.ripe.net/docs/route-collectors/), not guessed ASN coordinates. Counts show the filtered sample, not percentages of Internet coverage.

UAT required git, pinned tenacity 8.4.2, nginx API proxy/root configuration, and an execute-only ACL for `www-data` on `/home/david`. Its earlier source differed from the preserved production baseline only by Git metadata and normalized CRLF files; content was verified, backed up and adopted without overwriting source changes. The unrelated Windows checkout modification was untouched.

## Evidence

- 15 Python regression tests passed in both native Python 3.13 environments. They cover loop lifetime/context/shutdown, empty/stale/failing sources, validated prefixes and query limits, path prepends/loops/AS_SET handling, actual next hop versus observer IP, multi-address IPv6 persistence, write failures, and viewpoint-specific flap transitions.
- TypeScript/Vite production build, targeted live-UI ESLint, six existing preview state tests, and diff checks passed. The legacy bundle retains an existing size warning; the live entry is approximately 24 KB plus shared map/runtime assets before gzip.
- The first production attempt exposed multi-address IPv6 INET rejection after the next-hop correction. Scheduling was paused, the additive raw field and regression were added, and ingestion resumed only with the corrected candidate. UAT then persisted a real 50,000-update batch in 15.5 seconds, retaining 5,105 IPv6 next-hop pairs.
- Corrected scheduled production cycles completed at 16:57:25 UTC (50,000 updates, 33.4 seconds) and 16:59:02 UTC (50,000 updates, 40.58 seconds), with no batch-write or closed-loop errors in that interval. Source event time advanced from 16:57:11.410 to 16:58:48.510 UTC. UAT scheduled cycles also completed at 16:56:42 and 16:58:20 UTC.
- Production HTTP checks through nginx passed for IPv4/IPv6, origin ASN, exact prefix, collector, invalid prefix, and excessive time window. Successful query times were 10–421 ms in the initial production check. At 16:58:03 UTC, a 20,000-update sample contained 2,576 origins, 8,887 prefixes and 73 collector/peer viewpoints. These are sample observations, not global incident counts.
- Browser checks passed for live overview, search, 3D selection, prefix comparison, viewpoint persistence across reload, keyboard skip/focus, no console errors, and mobile layout without page-wide horizontal overflow. `docs/evidence/live-release/production.png` shows the actual production interface with fresh observations. The production browser tab was retained for David.

## Promotion and rollback

`scripts/deploy-native.py` implements artifact checksum checks, clean tracked-tree and ancestry checks, a stopped-scheduler requirement, and a hard drain gate for active/reserved/delayed tasks. Scheduler stops first; workers are stopped only after work drains. Old hashed assets remain available to open tabs. No broker queues or existing data were purged.

The original pre-release backup is `/srv/inet-health-backups/20261022T165107Z` (baseline `bd87a80`, including its known ingestion defect). Additional intermediate cutover backups are retained. The preferred recovery checkpoint after successful live verification is:

`/srv/inet-health-backups/20261022T1659-known-good`

It contains the verified `4635b71` source reference, frontend archive, nginx/service configuration, runtime window setting and a fresh observation snapshot. The installed helper is `/usr/local/lib/inet-health/deploy-native.py`. To roll back a future change, drain and then restore the verified checkpoint:

```sh
sudo /home/david/inet-health-engine/.venv/bin/python /usr/local/lib/inet-health/deploy-native.py drain
# If busy, let tasks finish and repeat the drain check; do not force-kill them.
sudo /home/david/inet-health-engine/.venv/bin/python /usr/local/lib/inet-health/deploy-native.py rollback /srv/inet-health-backups/20261022T1659-known-good
python3 /usr/local/lib/inet-health/verify-live.py http://127.0.0.1
```

Rollback itself was prepared and inspected, not executed against a healthy production service. An SSH command timed out while the final service transition finished remotely; subsequent Git, systemd, nginx, HTTP, journal and browser checks independently verified the installed commit and running services. No public DNS, TLS or firewall exposure was changed or claimed.

## Remaining roadmap gates

This release does not establish congestion, latency, loss, hijacking attribution, or global Internet health. A complete RIB, longest-prefix IP lookup, transit-AS search, broad/independent collector coverage, active diagnostics, validated RPKI freshness, incident lifecycle/correlation, historical replay, alerts and private ISP workspaces remain future work. AS_SET segments are currently represented as an empty path with an explicit limitation; the integer schema also cannot store ASNs above signed int32. Existing historical paths may have lost prepends.

Other legacy collectors have pre-existing limitations: RPKI/Atlas startup attempts ALTER operations without table ownership; missing optional dependencies/credentials and external source availability also affect them. They are not represented as verified performance evidence by the new interface. The older interface remains available as a transition aid and has not received the new interface's evidence-quality audit.

Before a broad public release, complete sustained retry/reconnect/soak testing, retention/capacity review for the measured write rate, bounded query concurrency/caching/rate limits, public API/auth/binding review, full accessibility/cross-browser testing, and reproducible environment/schema parity. Production had approximately 181 GiB free before cutover; this is not a retention guarantee.
