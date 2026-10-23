# Retention and analysis within 150 GiB local + 350 GiB NAS

Version 2.2 | 2026-10-23 | Prepared by OpenAI Codex

## Goal and changed scope

David set a planning envelope of **150G of container storage and 350G NAS attached**. This plan interprets G as GiB, matching the container allocation convention. Confirm byte quotas when provisioning; if the NAS uses decimal GB, reduce budgets proportionally. The combined allowance is 500 GiB and includes working space and reserves, not 500 GiB of observations.

Maintain **twelve rolling calendar months of analysis, incident history and selected evidence** within this allowance.

David confirmed that full-data retention is a development capability. Bulk raw archives and replay tooling belong to an isolated development/UAT namespace; they are not a production-facing feature or public export endpoint. Production's retained product remains trends, incidents and selected evidence. Internal ingestion buffers are distinct from making a full archive available. This does not add an unbounded or twelve-month full-raw promise to the 350-GiB budget. Keep routine detailed observations for a shorter, explicit period. This supersedes the full-detail twelve-month policy and the 8–12 TiB-per-copy estimate in [the earlier plan](RETENTION-AND-ANALYSIS-12-MONTH-PLAN.md). Those requirements cannot be assumed to fit this budget.

The tradeoff is material: after routine raw evidence expires, the tool can show retained trends, incidents, selected proof and checkpoint comparisons. It cannot replay every historical announcement, reconstruct a route at every arbitrary time or rerun a newly invented detector against discarded raw observations. An incident missed before raw expiry might not be recoverable later. The UI must distinguish measured history, retained evidence, summaries and unavailable detail.

This is a proposed policy and implementation sequence. No allocation, mount, filesystem shrink, data deletion, capacity-guard change or deployment is performed by writing it. Production currently has a 200-GiB root disk; UAT has 100 GiB. The host mapping for the supplied NAS path is now verified at `/Volumes/NAS1/storage/ai/ihealth_storage` on `//192.168.1.60/nas1`. CT protection prevented a direct bind attachment; it remains enabled and verified host-side publication is used for the pilot. Treat 150 GiB as the target operating budget; do not shrink the existing production filesystem to enforce it.

## Storage allocation

### Container: 150 GiB

| Purpose | Budget | Intended contents |
|---|---:|---|
| OS and application | 20 GiB | Runtime, dependencies, bounded logs and deployment checkpoints |
| Current routing state | 20 GiB | Selected maintained routes, indexes and current tombstones |
| Recent detailed data | 35 GiB | Approximately 24 hours of raw/normalized observations and recovery journal |
| Metadata and hot analytical cache | 15 GiB | Current source evidence, recent deduplication keys, catalogue cache and query summaries |
| Database working space | 15 GiB | WAL, temporary queries, controlled exports and migration work |
| Free-space reserve | 45 GiB | Recovery, bursts and maintenance; not normal retention capacity |
| **Total** | **150 GiB** | **105 GiB planned working allocation; 30% reserve** |

The recent-data allocation is shared by raw and normalized copies, not 35 GiB for each. The approximately 24-hour window is a sizing target that must pass profiling. PostgreSQL WAL and temporary-file correctness must not be compromised to force a bucket limit: pause lower-priority work/intake before exhausting required working space. Do not retain deployment backups or application logs indefinitely in the OS bucket.

### NAS: 350 GiB

| Purpose | Budget | Intended contents |
|---|---:|---|
| Rolling detailed archive | 120 GiB | Unique captured payloads and normalized events, compressed; initially 72 hours, seven days only after profiling |
| Twelve-month analysis | 50 GiB | Compacted trends, stable-cohort baselines and bounded analytical features |
| Routing checkpoints | 30 GiB | Compressed selected-peer checkpoints and required validation context |
| Incidents and selected evidence | 35 GiB | Deduplicated incident records, before/during/after proof and annotations |
| Catalogue and manifests | 5 GiB | Hashes, versions, coverage, file identities, expiry and checkpoint dependencies |
| Transfer/compaction staging | 20 GiB | Atomic publication, corrections and bounded job spill |
| Free-space reserve | 90 GiB | Archive outage, bursts and compaction recovery |
| **Total** | **350 GiB** | **260 GiB planned allocation; approximately 26% reserve** |

These are planning ceilings and admission budgets, not permission to erase records before their promised retention. The annual analysis/evidence/checkpoint buckets must be forecast against a full year. Spare space in the short-lived raw bucket cannot conceal an annual dataset that will exceed its allocation. Do not assume that NAS snapshots or backup copies consume no physical space.

## Proposed retention policy

| Dataset | Resolution/detail retained | Retention and location |
|---|---|---|
| Current routes and tombstones | Actual maintained selected-peer state | Local while relevant, independent of event age; safely compact obsolete generations |
| Routine raw observations and normalized changes | Full captured resolution, including session/source-state events | Approximately 24 hours local; 72 hours NAS initially; target seven days NAS after measured qualification |
| Global, regional, observer and AFI trends | One minute for seven days; five minutes for 30 days; hourly for twelve months | Hot cache plus NAS columnar summaries |
| ASN trends across observed networks | Hourly for 90 days; daily for twelve months | NAS, with coverage and metric definitions |
| Selected watchlist ASN trends | Hourly for twelve months | NAS; begin with a bounded cohort of up to 1,000 ASNs and admit expansion only after sizing |
| Performance measurements | Captured raw sample statistics for the routine-detail window; hourly for 90 days and daily for twelve months | NAS; retain hourly twelve-month trends for the configured anchor paths |
| Routing checkpoints | Full selected-peer checkpoint at the retained checkpoint times | Daily for seven days, weekly for 90 days, monthly for twelve months; preserve boundary/recovery dependencies |
| Correlated incidents and annotations | Structured lifecycle, confidence, impact, source lineage and detector version | Twelve months, including resolved incidents |
| Selected incident evidence | Relevant routes/measurements and validation context before, during and after the event | Twelve months within the evidence budget; explicit per-incident capture bounds |
| Configuration, gaps, quality and provenance | Source/cohort changes, coverage, schema/detector versions and archive references | Twelve months, with necessary dependency grace |

Retain twelve calendar months using UTC observation-time cutoffs; display the operator's selected time zone. Allow fourteen days of minimum ingestion grace for late-arriving annual records. Daily partitions/checkpoint seeds may therefore extend beyond the nominal window. Ordinary raw data does not inherit a fourteen-day minimum: its separately declared 72-hour/seven-day policy applies.

Daily ASN summaries should preserve announcement/withdrawal counts, instability candidates, prefix-count ranges, relevant origin/validation-change counts, eligible viewpoints and completeness. Global/regional hourly buckets preserve counts, minima/maxima, affected-entity estimates and measurement sample/packet denominators. Do not invent packet percentiles from sample medians, finer measurements than the provider supplies, or a precise timestamp hidden by a daily bucket.

Most noisy heuristic candidates become statistics, not a separate long-lived copy of the same raw event. Correlated incidents retain a structured proof bundle; larger raw windows are selected by severity/watchlist and deduplicated against shared objects. Late incident confirmation must pin available proof before routine raw expiry. If essential annual evidence approaches its allocation, pause source/feature expansion and surface the constraint; do not silently delete existing promised evidence or count missing evidence as recovery.

## Fit and growth tests

The last short-window measurement was approximately 16 GiB/day of indexed sampled BGP history. The earlier sizing model also used 5 GiB/day of journal growth as a placeholder, not a measured serialized rate. At a combined 21 GiB/day, seven days is approximately 147 GiB before archive conversion; 72 hours is approximately 63 GiB. A 2x reduction would make those approximately 74 GiB and 32 GiB respectively. These are sensitivity calculations, not measured compression claims, and other source payloads must be counted.

Therefore start with 72 hours of NAS detail. Promote to seven days only when measured compressed output plus duplicate representations, late data, burst recovery and staging fit the 120-GiB detail allocation. A useful steady-state gate is at most 12 GiB/day of total detailed archive output: seven days then occupies at most 84 GiB, leaving room within that bucket. Test bursts and export lag as well as the average. If it does not fit, publish the 72-hour policy rather than quietly shortening a seven-day promise.

For annual datasets, the allocations imply approximate daily averages of 140 MiB for analysis and 98 MiB for incident evidence. These are capacity checks, not daily deletion quotas. The checkpoint schedule retains roughly seven daily, thirteen weekly and twelve monthly points, with overlaps deduplicated; 30 GiB allows about 0.94 GiB per complete five-observer checkpoint on average across 32 non-overlapping points. Source-wide MRT downloads must not be confused with compact selected-peer checkpoints. Actual dependencies and file sizes determine the final count.

Use one authoritative retained observation per real source identity. Do not retain equivalent sampled and continuous copies merely because both collectors exist, but preserve every legacy-only viewpoint and ambiguous identity until coverage parity is proven. Distinct peers, probes, timestamps and validation contexts are not duplicates. Archive each Atlas sample once rather than repeating overlapping 25-hour response bodies at every poll. Record polling/status transitions separately.

The annual budget does not fund daily per-prefix history for the entire routing table, twelve-month full packet/measurement results or twelve-month complete route-update replay. Existing current-state and latest-provider tables require append-only short-term capture and permanent rollup/incident extraction before their values are overwritten.

## Architecture and protection

- Keep PostgreSQL and WAL local. NAS holds immutable compressed Parquet detail/summaries, lossless selected source records, checkpoints and manifests; do not move the live database onto an unqualified NAS mount.
- Add time partitions and a tested deduplication/replay design before rotating the existing journal. Preserve stable event identities and rollup watermarks across hot/archive boundaries, late arrivals and expiry. Current-state ordering and withdrawals must remain correct after older payloads expire.
- Export small committed batches; verify hashes, counts, readability and checkpoint dependencies before local expiry. Publish catalogue references atomically and make retries idempotent. Expiry of routine detail follows the declared policy; evidence pinned by an annual incident is a protected dependency.
- Use bounded Linux archive/rollup jobs with byte/CPU/memory/query limits. Vengeance is an authoring/control surface, not an always-on worker requirement. Serve annual queries from compact summaries and checkpoint/evidence indexes rather than scanning all raw files.
- Require a dedicated NAS quota and explicit mount identity/health checks. A missing mount must never cause archive files to fill an ordinary local directory at the mount path. On NAS failure, local capture has a finite buffer; pause safely before the reserve is consumed and record the gap.
- Treat backups separately from the 350-GiB primary dataset. A complete independent second copy is not allocated in this budget. Verify whether the existing backup service can protect the NAS data and catalogue, its physical capacity, retention and restore path before enabling destructive local expiry. Container backup coverage must explicitly include the NAS data; a normal container backup must not be assumed to do so. No extra 350-GiB copy is silently provisioned.
- If independent backup capacity is unavailable, the plan remains single-copy storage for older evidence and cannot claim protection against NAS loss. Keep that limitation explicit and hold destructive expiry until the selected protection/restore design is concretely reviewed. NAS snapshots on the same device are not an independent failure-domain copy.

## Phased implementation and gates

### Phase 0 — Measure within the fixed envelope

Inventory source identities, unique payload growth, existing coverage and overwrite behavior. Perform bounded compression/export trials on stored observations; measure per-day summary cardinality, selected incident proof sizes, checkpoint size, deduplication keys, WAL and temporary space. Select the actual NAS path/quota and verify backup inclusion without mounting or resizing as part of this planning document.

Exit: a forecast demonstrates all annual datasets fit their own budgets and the routine raw window fits 120 GiB under the agreed burst test. Set 72 hours as the initial detail policy; enable a seven-day promise only after the measured gate. Establish operator-visible warnings and the policy's exact published scope. Use bounded UAT fixtures/subsets rather than duplicating production's annual dataset.

If 150 GiB must be the physical allocation rather than the operating budget, plan a verified backup/restore migration onto a newly provisioned 150-GiB volume/container. Validate it in UAT and retain rollback before cutover; do not attempt an in-place root-filesystem shrink. That migration is a later implementation step, not an action authorized by this planning document alone.

### Phase 1 — Archive and extract analysis before expiry

Implement append-only short-term capture, versioned aggregates, structured incident records, selected evidence pinning, checkpoint rotation, immutable exports and the manifest catalogue. Preserve original source/environment provenance when importing existing canary data. Historical values already overwritten remain unavailable; no backfill is fabricated.

Exit: archive counts/hashes and representative replay reconcile, derived summaries can be reproduced from retained test detail, and a NAS/backup restore succeeds. Exercise interrupted export, late data, duplicates, checksum failure, NAS absence and evidence pinning. Measure compaction amplification inside the 20-GiB staging allowance.

### Phase 2 — Rotate safely and restore continuous intake

Run expiry in dry-run mode, then expire only verified eligible local copies and raw records beyond the declared window. Produce long-lived summaries and pin selected evidence before routine expiry. Replace the fixed 2-GiB canary cap with a measured byte budget inside the 35-GiB recent-data allocation; preserve the overall reserve and correct the masked capacity-stop reason.

Resume receivers after archival/protection/recovery boundaries are established and repair the recorded interruption with retained/provider archives where available. Gaps remain unknown until proven repaired. Retire redundant sampled storage only after preserving its unique viewpoints. Do not force-stop in-flight workers or purge queues.

Exit: a seven-day ingest run demonstrates bounded local/NAS usage, no unexplained sequence or aggregate-count loss, successful gap recovery and safe pause/restart under NAS failure. New/retained state remains correct after old payloads are removed.

### Phase 3 — Historical UX with honest detail limits

Add a twelve-month timeline and period comparison for global/regional and ASN trends, hourly configured path performance, incident history, selected evidence and retained checkpoint dates. Preserve shared resource/observer/time context across 2D/3D/topology and exports.

Label resolutions and the exact oldest/latest available detailed time. Older daily-only data must not appear as an hourly trace. Show 'raw detail expired under retention policy' separately from 'not observed', 'source unavailable' and a current collection gap. Only offer arbitrary route replay where the checkpoint and complete change interval are actually retained; older checkpoint comparisons must use their real timestamps.

Exit: reviewed investigations cross local/archive/summary boundaries without double counts or invented precision. Queries remain bounded under live ingestion; desktop/mobile/accessibility checks cover the degraded and expired-evidence states.

### Phase 4 — Twelve-month analysis and operations

Add day/week/month comparisons, stable-cohort baselines, recurrent degradation and incident lifecycle views using the retained resolution. Preserve detector versions and inputs/coverage; distinguish 'detected then' from 'reanalyzed now'. Full new-detector replay is available only for retained raw detail or pinned evidence. Historical validation uses the context retained then; today's validator cannot silently rewrite an old conclusion.

Run a thirty-day storage/collection soak plus labeled annual-volume fixtures and an accelerated expiry/dependency test. Test NAS disconnection, full quota, backlog, checksum corruption and restore. Alert on projected days to each bucket ceiling, annual forecast, archive lag, failed rollups, evidence growth and source gaps. Admit new viewpoints/providers only when their measured annual cost fits the fixed budget.

Exit: annual resolution/coverage and loss-of-detail behavior are accepted, backup protection is verified, usage remains below planned ceilings/reserves, and runbooks/restore limits are documented. Keep production changes separately concrete and reviewable under the existing authorization; this document itself changes no service.

## Operating rules

The container's 45-GiB and NAS's 90-GiB reserves are planned free capacity. Warn before a workload bucket reaches 80% of its allowance, require an operating response at 90%, and stop new lower-priority jobs/intake before the reserve is spent. Exact byte thresholds must account for filesystem overhead and tested in-flight work; do not rely on a delayed percentage check as a hard guarantee. Existing 20-GiB and 2-GiB guards remain until the replacement is implemented and verified.

Pressure responses are: pause expansion and expensive archive queries; drain verified eligible local data; let declared raw expiry proceed after extraction/verification; pause intake safely if needed. Do not silently shorten annual retention, delete incident pins or convert missing observations into a healthy result. Any later change to scope/resolution/windows must update the UI, manifests and ledger explicitly.

The next implementation slice is the Phase 0 fixed-budget profile and a non-destructive archive/rollup pilot. This plan creates a practical annual analysis service within 500 GiB; it does not claim twelve months of complete raw global Internet history.


## Implementation checkpoint — 2026-10-23

The development-only pilot preserved and verified all 1,828,156 stopped journal records on NAS: 1,553,458,889 serialized bytes became 117,921,645 compressed bytes (13.174x). A separate explicitly sampled profile covers legacy BGP, maintained routes and source evidence. This does not establish annual capacity or complete all-source retention. The hourly 72-hour size study has started; a separate finite UAT safety guard pauses future Celery beat scheduling below 30 GiB free without stopping workers or deleting data. The receiver first-stop cause is fixed, but the receiver remains paused under its unchanged cap.

See [RETENTION-PILOT-20261023.md](RETENTION-PILOT-20261023.md) for exact evidence, source roles, hashes, paths and backup/quota gaps. NAS protection/restore and safe expiry are still unqualified; pilot manifests explicitly prohibit expiry. Production and container sizes remain unchanged.

## Evidence capture and summary qualification — 2026-10-23

A bounded UAT qualification now preserves provider revisions atomically with latest-evidence writes. Atlas mesh samples are deduplicated by direction and observation time; overlapping 25-hour windows are not copied repeatedly. Built-in Atlas and IODA changes and collection cycles have the same capture hook. RPKI bulk snapshots, legacy BGP and maintained-route history are outside this new provider pilot. Built-in Atlas still polls provider latest results, so intermediate measurements that were never fetched remain unavailable.

The 72-hour capture policy ends October 26 at 23:26:53 UTC and caps accounted serialized content at 512 MiB / two million versions. This byte allowance includes an accounting overhead but is not an exact PostgreSQL disk quota; relation size, WAL and root reserve still require monitoring. Limits record explicit gaps while latest-evidence collection continues. Hourly/daily observed-cohort summaries are stored separately with correction versions, sample/packet counts, unknown coverage, and a processing watermark. No age expiry is enabled.

The first seed produced 700 hourly plus 28 daily mesh summaries; 728 summary records serialized to 681,554 bytes and compressed to 20,284 bytes, verified locally and on NAS. This is a small seed result, not a twelve-month cardinality forecast. Details, independent-backup proof status and outstanding gates are maintained in [RETAINED-EVIDENCE-20261023.md](RETAINED-EVIDENCE-20261023.md).
