# Development archive pilot — 2026-10-23

Prepared by OpenAI Codex. This is a non-destructive Phase 0/early Phase 1 increment under the 150-GiB local / 350-GiB NAS plan. Full bulk-data archival/replay is development-only. Production continues to use the established live sources and bounded investigation evidence; no full archive API or production NAS mount was added.

## Verified NAS location and boundaries

David supplied `/NAS1/storage/ai/ihealth_storage`. The verified host mapping is `/Volumes/NAS1/storage/ai/ihealth_storage` on Proxmox, backed by CIFS `//192.168.1.60/nas1`. The project directory and development namespace were created after checking the mount identity. A write/fsync/readback probe passed. Storage identity: `e6a0939db1dd4a12a5dbec00ccf45296`.

Archive placement is `development/uat554/profiles/` beneath that directory. `storage.json` records development-only full-data access, the proposed 350-GiB budget, backup/quota verification false and expiry disabled. This marker is not a server-enforced quota or access-control certification. The pilot enforces a separate five-GiB namespace cap and one-GiB compressed/four-GiB decoded bounds per full-journal run.

CT554 protection prevented hot-adding a read-only bind mount. No mount configuration was left pending, protection remains enabled, and no container was rebooted. NAS publication therefore uses a bounded host-side transfer/verification helper. The helper checks the real network mount before writing and refuses local-directory fallback. Full archive data was not attached to CT553/production.

## Full journal preservation result

The UAT receiver was already stopped at its existing capacity guard. A read-only, repeatable-read snapshot exported every row in `routing_messages`, comparing against the observer message counters and checkpoints. Verification checks each sequence exactly once, not just approximate row counts.

| Observer | Verified records |
|---|---:|
| Singapore / AS133210 IPv6 | 159,856 |
| New York / AS2497 IPv4 | 38,708 |
| Paris / AS15547 IPv6 | 1,430,135 |
| Sao Paulo / AS28624 IPv4 | 199,457 |
| **Total** | **1,828,156** |

Serialized record bytes: **1,553,458,889**. Compressed object bytes: **117,921,645**, approximately 112.5 MiB and **13.174x** smaller than the serialized records. This is not a ratio against PostgreSQL relation size, which also includes indexes and physical versions.

The profile is lossless for the exported database records, including source JSON payloads, high ASNs and complex path fields. It is not a claim to reproduce original network-wire whitespace/formatting that was not retained by JSONB ingestion.

Local run: `/var/lib/inet-health/retention-profile/runs/20261023T224000Z-2914b415`.
NAS run: `/Volumes/NAS1/storage/ai/ihealth_storage/development/uat554/profiles/20261023T224000Z-2914b415`.

Export/readback completed at 22:46:21 UTC (17:46:21 America/Chicago). Independent readback from the NAS copy completed at 22:49:15 UTC (17:49:15 Central). Every object checksum, decoded-stream checksum, record count and checkpoint sequence reconciled. Independent readback here means a second verification pass on the NAS copy, not an independent backup/failure domain. Objects and manifests remain `expiry_eligible=false`.

The UAT export used a 512-MiB memory/one-core budget, with observed memory peak 136.4 MiB. Host transfer/verification peaked at 246.8 MiB under the same 512-MiB/one-core bounds. Both jobs completed successfully. No database rows were removed or rewritten by the export.

## Other-source sample result

A second, explicitly sampled profile contains 10,000 legacy BGP rows, 3,736 maintained-route rows and 428 latest source-evidence records: **14,164 records** total. Serialized bytes: 8,066,204; compressed bytes: 633,422; combined ratio: 12.734x.

Run: `20261023T224640Z-9bfe8e90`, under the same local/NAS profile roots. Both copies verified successfully. BGP and route selection used reproducible block samples; source evidence used bounded latest records per source. This does not represent complete time windows, a full RIB checkpoint or unbiased traffic-rate sampling. It cannot establish annual capacity or extrapolate the journal ratio to every provider. Parquet layout, long-term aggregate cardinality and complete checkpoint costs remain to be measured.

## Finite growth study and safety pause

`inet-retention-study.service` runs read-only metadata/size measurements hourly in UAT. Its window is 2026-10-23 22:43:44 UTC through 2026-10-26 22:43:44 UTC, or October 23–26 at 17:43:44 Central. Measurements and its finite-window state are in `/var/lib/inet-health/retention-profile/`. It is not enabled at boot. A first sample is not a completed 24–72-hour capacity study.

A separate finite `inet-retention-capacity-guard.service` checks UAT root free space every ten minutes during the profiling period. Below 30 GiB it stops **only future Celery beat scheduling** and records the pause. It does not stop API/workers, purge queues, delete data or automatically resume beat. Its root-owned state is `/var/lib/inet-health-retention-guard/`; it is also not boot-enabled. This protects against the still-growing legacy sampled history while retention is unfinished. The first check observed sufficient free space and left beat active. This is a temporary UAT safeguard, not the completed all-environment capacity design.

## Receiver stop observability

The receiver now preserves the first shared shutdown cause while draining queued frames. A journal/disk capacity pause no longer becomes only `GracefulStop` when the reader exits. Signal shutdown remains distinct. The first-cause tests cover cross-observer shared stop state and a later signal.

Only the already-inactive isolated UAT receiver files were replaced. Backup: `/srv/inet-health-backups/retention-stop-20261023T224341Z`.
Runtime stream SHA256: `701a354d38db2ada3bcf19ea59eda04ca24d8b3610f927086b9b9a49f52b3d86`.
Stop-helper SHA256: `2ae2b98a90634c3a716208cf32737e754883603e7bf72591343c13318acbee63`.

The 2-GiB receiver guard, 20-GiB receiver/MRT free-space guard and observer budgets are unchanged. RIS remains paused; no forced restart was used to bypass its cap. RouteViews recovery and Atlas mesh continue independently. Native API/frontend/Celery application source remains a045888; isolated recovery/storage remains a9b6d49. No native worker restart or production deployment occurred.

## Verification and remaining gates

- 21 new tests passed on native Python 3.13, plus the existing 44-test suite: 65 tests total. New checks cover production/remote-DB refusal, exact roundtrip of high ASNs/complex paths, complete/duplicate sequence detection, compression/decoded bounds, checksum/count failures, traversal/symlinks, missing-mount fallback, namespace cap, first stop cause and beat-only safety behavior.
- Full-journal and other-source sample verification passed both locally and after NAS publication. Both manifests prohibit expiry qualification.
- Existing enabled weekly container backups land on NAS1. The inspected independent PBS schedules are disabled. Neither fact proves independent protection of this new NAS project directory; no backup job was enabled or changed. A normal container backup does not establish coverage of these host-side NAS objects.
- Server-side quota reservation, independent NAS backup/restore, Parquet query layout, complete checkpoint sizes, yearly summary/incident budgets, archive-backed deduplication, time partitions and safe expiry are still open. Do not treat one excellent compression result as proof that twelve months of all raw feeds fits 350 GiB.
- No production UI route, public bulk-archive endpoint, history expiry or automatic resumption of RIS has been enabled. Twelve-month production analysis remains the target, not a feature completed by this pilot.

Next: finish representative growth/format profiling, implement append-only measurement history and retained rollups/incident proof, verify protection/restore and safe recovery boundaries, then qualify local expiry and continuous-receiver resumption within the fixed budget.
