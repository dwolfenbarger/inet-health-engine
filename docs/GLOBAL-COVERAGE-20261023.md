# Global evidence coverage expansion — 2026-10-23

Prepared by OpenAI Codex. This increment expands the UAT canary from two to five configured routing observers and adds measured performance paths. Production remains PR #6/master19c2923. Configuration counts are not complete coverage or global health.

## Delivered to UAT

Application `a04588846b6e7190af6f3bc4e3d9bb1e088f9116` is live at http://192.168.1.75/. Frontend SHA256 `8971b4d6c918e46352a7155e8bbdcf9e5abe249bf3181e8c84dd937ed1db43ee`; release bundle SHA256 `2f5b16f24ae66e45fa0caa3f5f7f0c01c8b869f64d3eb67d2994fe7aaa85d191`. Application rollback: `/srv/inet-health-backups/20261023T082140Z`. Beat was stopped and active/reserved/scheduled work was zero before each worker restart. No queue purge or history deletion.

The **Where we have evidence** panel separates configured collector locations from measurement endpoints. Seven region rows show audited baseline-entry counts, reconstruction/gaps and recent/configured path directions. Prefix counts overlap across viewpoints and cannot be added as unique global coverage. Global coverage percentage and health score remain null.

The **Measured paths** tab provides 28 directed IPv4/IPv6 observations across seven anchors: Ashburn, Tangstedt, Feira de Santana, Cape Town, Singapore, Brisbane and Dubai. This selected bidirectional ring is not an all-pairs mesh or country-wide coverage. Source ASN is verified current probe metadata. Destination ASN comes separately from measurement metadata; 12 configured directions lack it and remain unknown. The ASN filter matches known endpoints only, without inventing destination metadata from an anchor's egress ASN. Routing prefix/window filters do not apply to these separate measurements.

`inet-atlas-mesh` reads existing public Atlas measurements approximately every five minutes, without scheduling probes or spending measurement credits. It stores bounded native ping statistics and source snapshots. Total packet loss and unknown RTT remain distinct. Current statistics cover 15 minutes; the baseline uses the preceding 25-hour window excluding those 15 minutes, requiring at least 60 samples spanning six hours. RTT is the median of sample packet medians; loss uses summed packet counts. Candidates require three current samples and nine sent packets, then flag latency exceeding both 10 ms and 25% of baseline, or loss increasing by at least five percentage points. These are measurement-change candidates; they do not establish congestion, failed links or ASN-wide outages.

Bounded `/api/v1/noc/mesh/{direction}` responses expose raw ping evidence. Traceroute links open actual public results for the same source probe and destination measurement. Automated traceroute/path-change analysis remains unfinished. `/mesh` and `/coverage` summaries omit raw series during routine refresh. Disconnected, failed, absent, stale and insufficient observations remain explicit, and retired configuration pairs are excluded.

Initial source checks refused eight directions involving an invalid Singapore anchor. AS132337/probe7166 replaced it after current probe/target identity validation. Repeated cycles now collect all 28 directions. Recent-sample qualification can vary independently: the 08:23 UTC live suite found 27/28 sufficiently sampled, with baselines for those 27; one sparse path was insufficient. At 09:05 UTC all 28 directions were sufficiently sampled and had baselines. Collection success never implies health or adequate coverage. A Guam-located US inventory entry was excluded from the mainland North American selection using coordinates and hostname.

## Routing status

At 09:15 UTC, all five observers had audited baselines. Four had completed recorded catch-up; São Paulo was waiting for archive publication/replay:

| Collector location | Selected peer | AF | Audited baseline | Current qualification |
|---|---|---|---:|---|
| Singapore / RIS rrc23 | AS133210 / 2001:de8:4::13:3210:1 | IPv6 | 261,911 | Reconstructed; live changes |
| Nairobi / RouteViews KIXP | AS30844 / 196.223.21.44 | IPv4 | 1,073,233 | Reconstructed archive; evidence through 09:00 UTC at this check |
| New York / RIS rrc11 | AS2497 / 198.32.160.42 | IPv4 | 1,074,569 | Audited 08:00 UTC RIB and catch-up complete; live changes |
| Paris / RIS rrc21 | AS15547 / 2001:7f8:54::156 | IPv6 | 256,233 | Audited 08:00 UTC RIB and catch-up complete; live changes |
| São Paulo / RIS rrc15 | AS28624 / 187.16.216.103 | IPv4 | 1,091,211 | Audited 08:00 UTC RIB; snapshot-to-checkpoint replay pending |

Collector cities describe observation locations, not route geography or physical traffic. These are selected peers, not every collector peer. All three new streams started before their 08:00 UTC snapshots. `bootstrap_not_before` avoids importing an older table and unnecessary replay. Public snapshots appeared approximately 08:14 UTC. Independent compressed-integrity/record-framing audits and selected-peer counts must match the decoded RIB before completion is recorded.

A completed snapshot records older live entries absent from that RIB as snapshot-absence evidence, preserving newer live changes without inventing BGP withdrawals. An idempotent generation-specific `SnapshotCatchup` interval then requires contiguous archives from the RIB timestamp to the captured live checkpoint, including after session resets. The view remains awaiting replay until every interval is applied. An unknown-start gap superseded by a later audited snapshot is not given an inverted start/end time. This is initial handoff reconciliation; periodic independent RIB reconciliation is still needed.

São Paulo finalization exceeded the database pool's 30-second command timeout at 08:39 and 08:59 UTC. Previously committed batches remained intact, but no completed baseline was published prematurely. A concurrent `(observer,event_at)` index is valid, and retry writes skip equal/older route tuples instead of physically rewriting them. A standard concurrent VACUUM/ANALYZE reclaimed obsolete MVCC versions from prior retries without deleting logical records/history; it completed successfully at 09:13:02 UTC.

The next retry completed the independently audited 1,091,211-prefix RIB at 09:12:52 UTC. At 09:14:44 UTC the remaining catch-up was correctly waiting for the provider's 09:10 update archive; live checkpoints continued advancing. The source's publication delay is distinct from the resolved finalization failures. Automatic strict replay continues as archives arrive. Full-table bootstrap delays other archive observers while parsing; fairer scheduling and sustained query/ingest capacity remain relevant.

## Runtime and recovery

The native application remains a045888. Isolated recovery/storage files match source commit a9b6d49:

- MRT SHA256 `ecd8e0d7fc77d458e1006aa525415738f93be80ff9392f6855a863683831c22d`.
- Store SHA256 `988ec7eb23f34d4cf3ff72b4edbaf45e821bcb4f35825344cde5881a8ce39fa8`.
- Atlas mesh SHA256 `f1ed06839be1ab07588059baac9adf64c519b9b2c5b4fb9259e346242fe8fa65`.

Recovery's requested SIGTERM drained naturally at 08:59:20 UTC. The hash-checked handoff then backed up the two old files at `/srv/inet-health-backups/routing-recovery-20261023T090018Z`, installed the reviewed files and restarted at 09:00:18 UTC. No process was forced. New recovery code checks for graceful stop between committed 1,000-prefix batches. `scripts/finish-routing-recovery-update.py` is UAT-only, bounded and refuses mismatched source hashes; its transient handoff unit completed successfully.

`inet-routing-stream`, `inet-routing-recovery` and `inet-atlas-mesh` run from `/opt/inet-routing-canary/collectors` and are not enabled at boot. Active configurations are `/etc/inet-health/routing-observers.json` and `/etc/inet-health/atlas-mesh.json`; inventories are versioned in `observers-uat.json` and `atlas-mesh-uat.json`. Original routing configuration backup: `/etc/inet-health/routing-observers-20261023T074504Z.json`. Four RIS receiver/six-total-observer budgets remain; five observers are configured. No startup DDL is performed. `20261023-observer-time-index.sql` must run outside a transaction because it builds concurrently.

Windows Git archive defaults can export CRLF despite core.autocrlf=false. Linux source transfers now use `git -c core.autocrlf=false -c core.eol=lf archive`, and tar-entry hashes are verified against Git blobs before deployment.

## Verification and capacity

44 Python tests, six existing preview-context tests, frontend build and targeted lint passed. Actual PostgreSQL regressions verify atomic failure/deduplication, unsigned ASNs, peer isolation, session invalidation, withdrawal ordering, longest-prefix fallback, snapshot absences, catch-up boundaries and physical tuple stability for unchanged replay. All test records were rolled back. Live nginx/API checks passed for seven regions, 28 directions, 14 IPv6 directions, endpoint ASN filtering, optional unknown target ASNs, bounded raw evidence and invalid inputs. Served HTML references the expected new assets. Rendered browser/accessibility review is still pending; no certificate warning was bypassed.

A 32-request LAN smoke check at concurrency four completed without errors during bootstrap: median 0.154s, p95 3.534s, maximum 4.022s. A repeat after cleanup/snapshot completion also had no errors: median 0.152s, p95 4.218s, maximum 5.680s. Latency did not demonstrate an improvement, and the tail remains a serving-capacity concern. These are small warm smoke checks, not sustained public-load testing or a passed subsecond SLO.

At 09:15 UTC: root free 70.52 GiB; routing relation including indexes 5.08 GB; raw journal 337 MB; source evidence 793 MB; legacy sampled BGP history 11.87 GB. Relation growth includes retry write amplification and indexes, not only unique routes. Existing 2-GiB journal and 20-GiB root-free guards pause ingestion. Retention/archival is not implemented; no historical data was deleted. Recoverable retention, throughput and service/query budgets must precede broader sustained ingestion.

## Remaining gates and next increment

1. Let São Paulo catch-up finish as the remaining source archive is published, verify every interval, and qualify sustained continuity across restarts, database failures and backpressure.
2. Implement recoverable retention/archival and measure ingestion/serving contention before increasing observers.
3. Add periodic independent RIB reconciliation, both families in target regions and routing viewpoints in Oceania/Middle East. Provider per-peer hourly MRT prototypes may reduce bootstrap cost but have not been integrated or availability-qualified.
4. Expand performance endpoint/network diversity, traceroute comparison, persistent incident lifecycle and independent routing/performance corroboration.
5. Complete rendered desktop/mobile/accessibility/NOC-display review and public API/security controls. RPKI refresh reliability, Radar access and additional measurement providers remain separate work.

Production promotion is not complete. Existing production authorization persists; these technical gates qualify what can responsibly be released. Global health remains unknown.

References: [RIS collector metadata](https://ris.ripe.net/docs/route-collectors/), [RIS MRT archives](https://ris.ripe.net/docs/mrt/), [Atlas anchor mesh](https://atlas.ripe.net/docs/tools-and-code/anchor-mesh-status/), [Atlas ping statistics](https://atlas.ripe.net/docs/apis/rest-api-reference/measurements/measurements_ping_stats), [RIS per-peer MRT prototype](https://ris.ripe.net/docs/prototypes/per-peer-dumps/).
