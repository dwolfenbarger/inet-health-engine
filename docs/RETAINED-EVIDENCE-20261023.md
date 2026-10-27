# Provider history and observed-cohort summaries — 2026-10-23

Prepared by OpenAI Codex. This is a bounded, non-destructive UAT qualification under the 150-GiB local / 350-GiB NAS plan. Full archive tooling is development-only. Production application, frontend and native API/worker source are unchanged.

## Implemented behavior

Latest provider evidence previously overwrote its earlier values. Additive PostgreSQL triggers now capture accepted Atlas, Atlas mesh and IODA revisions and collection cycles in the same transaction as the latest-table write. A collector transaction rollback also rolls back its history. Application users can select evidence but cannot rewrite/delete it or invoke the privileged capture functions directly. The schema migration is disabled by default; only the UAT installer enables a finite qualification window.

A logical observation is identified by provider, evidence kind, provider identity and observation timestamp. Identical consecutive content is suppressed; a correction appends a version, including A → B → A reversions. SHA256 includes payload and ASN/family/resource metadata. Atlas mesh samples are stored individually by direction and timestamp. Repeated overlapping 25-hour result arrays are not archived on every poll. Small direction snapshots and collection-cycle records preserve interpretation and polling state separately. Zero-received samples retain their packet loss and an unknown RTT.

The new provider pilot excludes bulk RPKI snapshots, legacy sampled BGP and maintained-route history. Their existing datasets remain intact. Built-in Atlas still requests the provider's latest result per probe: measurements never returned by that polling strategy cannot be recovered by a database hook. This is append-only history of accepted writes, not a complete public Atlas feed. IODA retains native event corrections and source lineage; provider candidates do not become confirmed incidents.

## Qualification bounds and operation

Capture started **2026-10-23 23:26:53 UTC** (18:26:53 America/Chicago) and ends **October 26 at 23:26:53 UTC**. The shared pilot budget is **512 MiB of accounted serialized content**, **two million versions**, and **64 KiB per record**. Accounting includes an overhead allowance; it is not an exact PostgreSQL disk quota. Monitor relation/index/WAL growth and the existing root-space guard independently. Policy counters and admission decisions are serialized inside the write transaction.

Once a limit/window is reached, explicit source/reason gap counters record skipped candidates. Latest-table ingestion continues. No rows expire and no budget is silently extended on an installer rerun. Unexpected database failures roll back the collector transaction and remain collection failures. Native collector timeouts and migration lock timeouts remain bounded.

UAT installed the additive schema and seeded only the 28 current mesh direction rows, including their available rolling sample windows. Other historical latest-table contents were not passed off as a full backfill. No worker, queue, beat, routing receiver or production service was restarted for this installation. The dedicated summary process was restarted after its reviewed normalization fix; its transactions are atomic.

`inet-retained-summaries.service` computes hourly and daily UTC source/family/cohort summaries over a bounded recent 48-hour observation window, rebuilding them idempotently for corrections. It stops at the policy deadline and is not enabled at boot. It has a 256-MiB service limit and 30-second database statement limit. Older corrections require explicit bounded backfill; the worker does not promise arbitrary old-data reconciliation.

Each summary records sample counts, identities, revision counts, first/last samples, valid packet counters, RTT sample counts, algorithm version and a captured-ID watermark. Mesh RTT is the median of sample medians; built-in Atlas uses the mean of the returned sample RTT fields. Loss is weighted by valid packet counters. IODA counts event-start candidates, not a health probability. Expected coverage remains unknown and `complete=false`; no global score or missing-as-healthy inference is added. Buckets with no observed samples are absent, not zero-valued health results.

To pause capture without deleting evidence, a database administrator can set `inet_retention.policy.enabled_until` to the current time. Future skipped candidates remain visible as gaps. Stop the dedicated summary service separately if needed. Rollback does not require dropping data or restoring a database dump.

## Verified results

| Evidence | Result |
|---|---:|
| Initial mesh hourly summaries | 700 |
| Initial mesh daily summaries | 28 |
| Summary export records | 728 |
| Serialized summary bytes | 681,554 |
| Compressed summary bytes | 20,284 |
| Retained versions at the qualification status check | 10,593 |
| Accounted capture bytes at that check | 4,977,944 |
| Retention schema relations including indexes | 18,178,048 bytes |
| UAT free root space | 63,686,901,760 bytes |
| Recorded capture gaps at that check | 0 |

The status check included 10,534 mesh sample versions, 56 direction snapshots and three collection-cycle records. Built-in Atlas and IODA collection-cycle writes were captured, but no new evidence snapshots had arrived from them at that check. Empty/error collections must be inspected through their source state; they are not healthy observations. The initial mesh seed includes timestamps before capture began because those samples were still present in the rolling provider payload.

Summary run: `20261023T232806Z-3f963723`, under the existing local profile root and NAS `development/uat554/profiles/`. The export and NAS readback verified checksums, decoded contents and all 728 records. The 33.601x compression ratio is against this small serialized summary set, not a yearly capacity forecast. Gzip JSONL remains the pilot format; Parquet/columnar query layout is still open.

Validation: 21 existing retention/guard tests and the 44 native tests passed. A disposable PostgreSQL 17 database also passed **23 integration checks**, including migration rerun, disabled default, deduplication, corrections/reversions, high ASN, transaction rollback, overlapping samples, excluded bulk RPKI, cycle capture, hourly/daily summaries, weighted loss, unknown counters/coverage, idempotence, byte/record/size/window admission limits application-trigger execution, and append-only privileges. The final 1,000-insert qualification took 0.632 seconds; this is a small isolated check, not a sustained production throughput benchmark. The disposable database was removed afterward.

Migration SHA256: `96b71afbcc09510552e80adb14d235d6db0e2b1a602f4dd07f7f62bb168feae0`.
Summary script SHA256: `55c00c54b6de659ef7653b8b1a64eb54d422c54ee29c26e65dded4abce3f9ba0`.

## Independent backup and restore proof

The existing independent PBS datastore `webster` at `192.168.1.229` had approximately 426 GiB available at the check. A targeted project-only backup used the existing protected storage identity and certificate fingerprint. Credentials stayed inside the host process. No fleet backup schedule was enabled or altered.

Snapshot: **`host/inet-health-development/2026-10-23T23:31:07Z`**. Restore/readback completed at **23:33:38 UTC** (18:33:38 Central). All **58 files / 118,599,197 bytes** matched SHA256 after full restore to temporary host staging. All three restored profile datasets decoded and verified: **1,828,156 journal records**, **14,164 mixed sample records**, and **728 summaries**. Temporary restore staging was removed after verification. The job completed under a 512-MiB / 50%-CPU budget, with a 377-MiB memory peak.

The proof is recorded in the NAS project root as `backup-readback.json`. This is a separate-device point-in-time recoverable copy. It does not prove ongoing scheduled coverage, reserved annual backup capacity, a backup retention policy or administrative/geographic independence. Existing fleet PBS schedules remain disabled. The NAS storage marker therefore still correctly says `backup_coverage_verified=false`; all profile/proof records remain `expiry_eligible=false`.

## Source-health follow-up

A subsequent UAT check showed Atlas/mesh/RPKI collection timeouts. Journal evidence establishes legacy sampled-BGP write timeouts before the capture policy began (23:19–23:25 UTC) and stale-validator errors at 23:22/23:25. The inspected database activity had no blocked sessions or retention-policy lock wait; active sampled-BGP writes were waiting on database I/O. This single snapshot does not establish the full cause or certify capture overhead under sustained load. Atlas mesh subsequently completed **28/28 directions at 23:39:40 UTC**. Keep native collection/backpressure and validator freshness as separate release gates; active services alone do not prove fresh evidence. No timeout was relabeled as healthy and no queue purge, forced restart or guard bypass was used.

## Remaining gates and next work

1. Resolve/requalify native sampled-BGP write throughput/backpressure and validator freshness alongside the finite 24–72-hour growth study and representative complete checkpoint/columnar format measurements. Forecast annual ASN/region/routing/incident cardinality rather than extrapolating this mesh seed.
2. Verify/enforce the 350-GiB NAS allocation and reserve; qualify ongoing scoped backup coverage, retention and restore monitoring. Shared free capacity is not a reserved quota. CT protection still prevents direct attachment and remains enabled; the verified host-side publisher continues to avoid local fallback.
3. Add routing/ASN/region/incident rollups, selected incident proof and retained checkpoints. The present observed-provider cohorts are the first analytical layer, not the full twelve-month product.
4. Qualify time partitioning, archive-backed deduplication, checkpoint/recovery dependencies and safe journal rotation before enabling local expiry or resuming RIS. The original two-GiB receiver guard is unchanged, and its interruption remains recorded.
5. Add bounded historical APIs and UX with explicit observed/expired/unavailable states, then complete annual-volume, failure and restore soak qualification before production promotion.
