# Maintained ASN investigation release

Prepared by OpenAI Codex. Production application promoted at 04:48 UTC on October 24, 2026; production browser, native API and UAT refresh evidence are recorded below.

The recent-update view hid stable networks and transit ASNs and capped route browsing at 200 rows. Maintained routing is now the default. A paginated ASN directory searches the full captured inventory; route browsing distinguishes origin, ordered transit and collector-peer appearances. ASN detail combines registry holder provenance, role/prefix counts, route attributes, local origin validity, existing anomaly candidates and access to measured-path evidence. Missing data remains explicit.

## UAT evidence

Application: `40e5b07e694a0a21af26a824bf4e8cd1db8805ce`.
Rollback: `/srv/inet-health-backups/20261024T041639Z`.
Generation 2 was captured at 03:39 UTC and published at 03:52 UTC on October 24, 2026.

| Check | Result |
|---|---|
| Inventory | 86,972 observed ASNs; 86,589 origin ASNs; 1,373,396 distinct prefixes; 3,771,205 per-viewpoint routes across five views |
| Entire directory | All 86,972 entries reconcile; 872 directory requests including validation cases |
| Stable ASN | AS1 remains discoverable with no sampled updates during the checked 15-minute window |
| Complete ASN route browsing | All 13,343 AS13335 origin entries reconcile without duplicates or omissions; UI browsed past row 200 |
| ASN roles and identity | AS174, 1299, 2497, 3356, 6939, 13335, 15169, 15547, 16509, 28624, 30844 and 133210; all four role queries and metadata passed |
| Transit-only ASN | AS33891 renders its holder identity and 12,188 transit entries across five views; selecting Origin correctly returns zero, and Transit restores the routes |
| IPv6 and four-byte identity | AS133210 retained; IPv6-wide and scoped queries passed |
| Failure semantics | Invalid/changed-filter cursor: 422; expired generation: 409; absent ASN explicit. Unit-tested metadata failure retains routing evidence; stale capture is marked stale; the real one-hour threshold was observed in the UAT browser while refresh was running |
| Native browser | Directory/search/pagination, transit-hop AS2497, 1.1.1.1 lookup and three-view comparison, IPv6 AS15547/rrc23 filter and reload context, absent ASN4294967295 rendered |
| Measured evidence | AS33891 has no matching configured measured path; the UI explicitly withholds a health conclusion. The 3D collector globe renders and its control is keyboard reachable |
| Narrow layout | 390px viewport, no document-level horizontal overflow; wide tables scroll within panels |
| Automated checks | 90 Python regressions, frontend build and changed-file lint passed |

Timing from 1,006 requests in 79.76 seconds with live collection: overall p95 0.40s, maximum 1.42s. The 121 route requests had p95 0.758s and maximum 1.415s. The 872 directory requests had p95 0.061s and maximum 0.255s. The 13 identity requests had p95/maximum 0.248s; metadata was warm from preceding qualification. This is single-operator qualification, not a public concurrency or capacity certification. Raw evidence: `asn-uat-qualification.json`. Screenshot: `uat-asn-mobile.jpg`.

## Storage and refresh

The catalogue is derived serving data, not a new telemetry source or archive. Each generation copies eligible current observer routes under repeatable-read isolation, builds sorted ASN memberships and summary counts, then publishes atomically. Route pages carry capture time and generation-bound cursors. Live intake continues independently; current observer continuity and validation have their own clocks.

The initial UAT route copy/index/aggregate took 822.62 seconds, and the subsequently qualified membership backfill took 312.02 seconds. The integrated production builder published 3,771,374 routes in 1,583.01 seconds (26.38 minutes) while collection and the UAT build were running. The integrated UAT refresh completed in 1,956.97 seconds (32.62 minutes): generation 3 captured at 04:20:30 UTC, marked published at 04:52:39 and committed by 04:53:07. Its 86,971 ASNs and 3,771,344 routes are distinct from the earlier snapshot. The pinned generation-2 first page and next-page cursor remained unchanged and duplicate-free after generation 3 was published (`REFRESH_CURSOR_PASS 2 new 3`). Two published generations occupy 12,840,738,816 bytes in route/membership relations, leaving approximately 44 GiB root space free on UAT. Because building a snapshot takes substantial time, capture age can exceed one hour between publications. Improving refresh cost and cadence remains open. The timer waits 15 minutes after a build finishes; it does not promise a 15-minute capture interval. Captures older than one hour are marked stale. The last two generations are retained; expired cursors explicitly request refresh.

Admission reserves at least 8 GiB for an incoming generation, keeps 35 GiB root space free, checks a 24 GiB peak derived route/membership relation budget before and after phases, and uses 900-second query and 4 GiB temporary-file limits. These are application admission/refusal guards, not filesystem quotas. PostgreSQL resource limits are separate from the 512 MiB Python service limit. Failed transactions retain the last published view. Cleanup runs before admission to prevent excess old derived generations from blocking refresh. Source routes, checkpoints, gaps and journals are not expired by this builder. The existing 150 GiB local / 350 GiB NAS planning policy and private development archive policy remain in force.

## Scope still open

This release exposes maintained evidence; it does not measure every network's performance or establish a global health score. Organization-name search, richer observed adjacency detail, evidence-correlated incident lifecycle, congestion attribution, historical serving and broad independent performance coverage remain future work. The 2D/3D maps show collector geography; AS-path topology is a control-plane observation. Annual retention and public capacity qualification remain open. Backups and new feeds remain held.

## Production rollout

Production at http://192.168.1.113/ runs `40e5b07e694a0a21af26a824bf4e8cd1db8805ce`, promoted at 04:48 UTC on October 24, 2026. Rollback: `/srv/inet-health-backups/20261024T044822Z`. The scheduler stopped and workers reached zero active, reserved and scheduled tasks before installation. Existing RIS/recovery/mesh services continued; API, worker and scheduler are active after promotion.

Generation 1 was captured at 04:15:22 UTC and published at 04:41:45 UTC: **86,975 all-role ASNs, 86,592 origins, 1,373,564 unique prefixes, 3,771,374 routes and five viewpoints**. All directory entries and all 13,341 AS13335 origin routes reconcile, along with the same 12-ASN role/identity and negative-case checks used in UAT. Quiet AS1 is discoverable.

The first candidate run had overall p95 0.427s and route p95 1.59s, with a 10.885s cold IP-lookup outlier. An immediate repeat of that IP lookup took 0.111s. The full deployed nginx/API run then passed all 1,006 requests in 70.27 seconds: overall p95 0.322s, maximum 1.038s; route p95 0.521s, directory p95 0.064s, identity maximum 0.033s with warm metadata. The cold outlier remains disclosed; this is not a concurrent public-load certification. Evidence: `asn-prod-candidate-qualification.json` and `asn-prod-native-qualification.json`.

Production's bounded catalogue timer is enabled and active; its next generation is building. The source bundle SHA256 is `2746a8d94c9a31a4766a864073572e8e3ce357989d1586cb44f9fe176c298ce1`; frontend SHA256 is `7702f18f47eb9ce1ce5727ca8f4de186b271e22d4f0fc1f1aafc1e26dc738749`. Root free space was approximately 143 GiB after initial publication. No source journals, queues or evidence were purged.

Production browser verification: directory page 2 starts at AS68; AS33891 has 12,166 transit entries and zero origin entries. Its first rows on route pages 1/2/3 were 2.27.169.0/24, 37.9.253.0/24 and 62.60.144.0/24. Clicking topology hop AS41689 opens 6,261 transit entries despite zero origin entries. AS15547 IPv6/rrc23 filters render 2001:14a8::/32 through AS133210 and AS6939, and survive reload with topology mode intact. IP 1.1.1.1 resolves to 1.1.1.0/24 from three viewpoints; comparison preserves distinct paths and RPKI evidence. A 390 × 844 production viewport shows AS133210 identity and role counts without document-level overflow (375px document width). The absent ASN4294967295 shows explicitly unobserved with registry name unavailable. Browser console errors: none observed. Viewport was reset. Screenshots: `prod-asn-desktop.jpg` and `prod-asn-mobile.jpg`. Temporary candidate API services were stopped after qualification; operational collectors and refresh/rotation timers remain active.
