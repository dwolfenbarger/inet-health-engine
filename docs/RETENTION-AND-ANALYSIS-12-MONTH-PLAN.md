# Twelve-month retention and analysis plan

Version 1.0 | 2026-10-23 | Prepared by OpenAI Codex

## Outcome and scope

Maintain a rolling twelve calendar months of captured Internet-health observations, searchable analysis and supporting evidence. An operator must be able to investigate an ASN, prefix, observer, measured path or incident at a past time; compare periods; and inspect what supported a finding. Older detailed observations remain available, rather than being replaced entirely by summaries.

This is a proposed implementation plan. It records the target policy and acceptance gates; it does not enable deletion, resize disks, change ingestion limits, purchase storage or promote the UAT release. Current source coverage remains partial. Retention cannot turn selected viewpoints into complete global visibility.

The twelve-month window covers what we actually collect, at the source resolution available to us. Native Atlas ping statistics, for example, are not individual packet records. It does not promise all Internet traffic, every BGP peer or measurements never performed. We currently have days of collected history. Twelve months of continuously observed history accumulates over time; older provider backfill must be availability-qualified and labeled separately.

## Verified starting point

Read-only audit on October 23, approximately 14:17 America/Chicago:

- UAT: 100 GiB provisioned disk, approximately 62 GiB available; PostgreSQL database 25.4 GiB. Sampled BGP history 17.6 GiB, maintained route relation 5.0 GiB, raw journal just over 2 GiB, source evidence approximately 0.75 GiB. Relation sizes include indexes.
- Production: 200 GiB provisioned, approximately 158 GiB available; database 18.7 GiB. Sampled BGP history is approximately 17.7 GiB.
- UAT has PostgreSQL 17 without TimescaleDB. Production has PostgreSQL 17/TimescaleDB 2.31.0. No application-data age-retention or compression jobs are configured; Timescale's internal job-history expiry does not expire observations.
- All five selected routing observers completed initial reconstruction. Four RIS receivers subsequently stopped cleanly with their journal at the 2-GiB capacity guard; those interruptions remain visible. RouteViews and Atlas collection continue.
- The 20-GiB root-free guard protects the new receiver and MRT downloads, not every legacy collector or database writer. The sampled BGP collector continues growing beside the new journal.
- Sampled BGP relation growth over roughly ten hours was approximately 16 GiB/day. This is a short-window, index-inclusive measurement, not a sustained global-feed estimate.
- Some provider evidence is stored as the latest value per identity. Longer retention alone will not recover overwritten measurements; append-only history capture must be added.
- The Proxmox local thin pool has approximately 1.06 TiB available, shared with other fleets. No dedicated archive allocation or independent archive copy is established.

Current review branch: `openai/continuous-routing`, draft [PR #7](https://github.com/dwolfenbarger/inet-health-engine/pull/7). Production remains PR #6/master `19c2923`.

## Proposed retention policy

The minimum is twelve calendar months by observation time, using a UTC cutoff, with a fourteen-day minimum retention after ingestion for late arrivals. Keep older checkpoint seeds and dependencies when necessary to reconstruct the beginning of the retained window. Expiry advances only after the dependency catalogue confirms it is safe; it is not a blanket deletion at midnight.

| Data class | Fast local retention | Retention through twelve months | Important boundary |
|---|---|---|---|
| Raw routing messages and unique captured provider records | 72-hour recovery buffer | Compressed, lossless observation payloads plus provenance | Archive acknowledgement and recovery-safe sequence required before local expiry |
| Normalized routing changes, session events, measurement samples and source-state transitions | Seven days indexed | Partitioned, compressed queryable detail at original captured resolution | Preserve distinct observers and unique observations |
| Current maintained routes and withdrawal tombstones | Active state, independently of event age | Versioned daily checkpoints plus retained changes | Age alone cannot delete a current route or resurrect a withdrawn one |
| RIB/bootstrap and reconciliation evidence | Latest usable checkpoints | Own retained checkpoints/source evidence, hashes and import manifests | Keep a valid seed at or before the oldest replayable time |
| One-minute aggregates | Thirty days online | Recomputable from retained detail | Do not lose peaks, counts or coverage in averaging |
| Five-minute aggregates | Ninety days online | Recomputable from retained detail | Older fine-resolution queries may run as archive jobs |
| Hourly/daily aggregates and baseline features | Entire twelve months online where measured capacity permits | Entire twelve months | Include eligible-observer denominators and metric definitions |
| Incidents, annotations, detection versions and evidence references | Entire twelve months searchable | Entire twelve months, including resolved incidents | Pin evidence for active investigations that extend beyond ordinary expiry |
| Source configuration, observer identity, gaps, algorithm versions and archive catalogue | Entire twelve months searchable | Entire twelve months plus dependency grace | Preserve what was knowable at the time |

Periods overlap deliberately: recent records can exist in the database and archive while handoff is verified. Days 8–90 form a frequently queried archive tier; days 91–twelve months form the older tier. Both retain full captured detail. These can initially use the same online archive storage, rather than introducing a retrieval-delay service. The second copy retains the same twelve-month dataset; it must not expire before the primary.

## Storage and processing design

1. Keep live serving/current state and short-lived indexed history in PostgreSQL on a dedicated data volume. Use time partitions for append-only history. Make the PostgreSQL/Timescale differences explicit in migrations and exercise production-equivalent behavior in UAT; do not assume hypertable policies work on plain PostgreSQL.
2. Export immutable normalized observations to Parquet with Zstandard compression; retain captured source payloads losslessly either in those files or in referenced compressed payload objects. Count both representations in capacity tests if both are stored. Preserve uint32 ASNs, IPv4/IPv6, complex paths, session generations, source event time and ingestion time.
3. Partition archive files by dataset/source and time, with bounded observer/AFI subdivisions where measured file size warrants it. Maintain file-level event-time ranges and entity statistics. Avoid one tiny file per ASN/prefix. Late arrivals publish correction files rather than silently changing sealed history.
4. Store hashes, schema versions, record counts, byte sizes, observer/generation sequence ranges, source URLs, gaps and dependency references in manifests. Publish immutable objects, verify them, then atomically publish the catalogue reference. An interrupted export must be resumable and idempotent.
5. Query the archive through a separate, resource-bounded Linux analysis worker, initially evaluating DuckDB over Parquet. Use server-side filters and explicit query budgets; large reconstructions are asynchronous jobs. Neither archive processing nor collection requires an always-running Windows/Vengeance worker.
6. Keep an independent archive copy outside the primary host/storage failure domain, with separately recoverable manifests and credentials. A second directory or snapshot on the same thin pool is not the second copy. Storage access controls must also prevent a primary-side lifecycle mistake from deleting both copies immediately.

Archive each unique provider sample once, retaining later corrections as versions and preserving polling/source-state metadata. Do not archive an entire overlapping 25-hour Atlas response again every five minutes merely to retain the same samples. Provider request envelopes can reference existing observation objects. Validation results reference retained, versioned validator material rather than copying a million-entry validation dataset into every result. Profile these sources explicitly; deduplication must not merge different probes, observers, timestamps or validation contexts.

Production will own the annual operating dataset. UAT uses a bounded representative subset and an isolated namespace, rather than a second twelve-month database. Preserve provenance for the existing real UAT canary observations and never count their replay as an independent production source. The independent archive copy remains mandatory even when UAT has a subset.

PostgreSQL supports time partitioning and partition-level maintenance. However, partitioned unique constraints must include the partition key: the current `(observer,message_id)` journal key cannot simply be moved into date partitions without redesigning deduplication. Preserve a compact durable identity/replay ledger and test duplicates across partition boundaries and hot/archive transitions. Budget its index growth separately. [1]

Parquet filter/projection pushdown is a useful basis for reading only relevant columns and ranges; it is not a latency guarantee for a twelve-month query. Actual file layout and concurrency must be benchmarked. [2]

## Capacity envelope and decision gate

Do not extrapolate a compression ratio from database size. Current measurements include indexes and physical row versions; the archive changes both representation and indexing. Measure exported bytes, query indexes/catalogues, checkpoints, deduplication keys, temporary compaction space, WAL and backup growth separately.

For planning only, use 366 days and two provisional input equivalents: 16 GiB/day for the sampled-history relation and 5 GiB/day for a raw-journal allowance. The latter is an unverified sizing placeholder informed by the canary reaching approximately 2 GiB in hours; it is not a measured serialized feed rate. Keeping both gives approximately 7.5 TiB/year of index-inclusive input equivalent before archive conversion. Distinct source coverage must be preserved; overlap cannot be removed merely because two observations mention the same route.

The table is a sensitivity calculation, not a codec benchmark. The reduction factor includes omitted database indexes, normalized encoding and compression. Reserve 0.5 TiB for checkpoints/aggregates/catalogue and 0.25 TiB for working space as provisional allowances, then divide by 0.70 to retain 30% free capacity. Re-measure every allowance in Phase 0.

| Achieved archive reduction relative to the input equivalent | Annual event/payload archive | Approximate capacity per archive copy, including allowances and 30% free |
|---|---:|---:|
| 1x | 7.5 TiB | 11.8 TiB |
| 2x | 3.75 TiB | 6.4 TiB |
| 4x | 1.88 TiB | 3.8 TiB |

Initial budgeting envelope: approximately 500 GiB of fast database/working storage and 8–12 TiB usable per independent archive copy until profiling justifies a smaller allocation. Two such archive copies mean 16–24 TiB usable in total, before the separate operational backup budget. These are usable capacities after storage redundancy, not raw disk labels or a purchasing decision. The lower envelope assumes measured reduction; it is not guaranteed under the 1x case.

This covers a conservative continuation of the current selected-feed footprint, not the eventual full global scope. Additional peers, protocols, measurements, incident storms or retained source snapshots require a revised forecast. Model per-source growth and measured burst distributions; require storage and CPU/query headroom before expanding ingestion. Do not silently shorten retention or reduce evidence to meet a budget.

## Phased execution

### Phase 0 — Stabilize and measure

Deliver a source-by-source capture inventory and 24–72-hour sizing study, including read-only export/compression trials on representative already stored data. Measure average/peak event rates, indexed growth, serialized/compressed bytes, daily checkpoint size, replay throughput, deduplication storage and backup amplification. Capture one update surge or use labeled stress replay if none occurs. Produce per-source forecasts for current, 2x and 5x ingestion, with capacity limits.

Map sampled versus continuous coverage before deciding which stores are redundant. Retain legacy-only viewpoints; ambiguous legacy identity cannot be treated as a proven duplicate. Define append-only provider history so Atlas/RPKI/IODA/source-state changes stop disappearing behind latest-value upserts.

Preserve the existing journal and current checkpoints while collection is paused. Correct capacity-pause observability so the initiating reason survives the final graceful-stop status. Specify all-writer disk safeguards, operator alerts and recovery steps. If interim storage is required to complete measurement, make that a bounded implementation change with verified placement and rollback; this document makes no allocation.

Exit: measured capacity report, selected primary/independent-copy locations and proposed expiry configuration. The existing approximately 42-GiB margin above the canary's disk guard is too small to treat the profiling period as an unrestricted ingest run. Current guard values remain until the archiver and replacement byte budget are validated.

### Phase 1 — Build durable historical capture

Add append-only observation/source-status history, durable identities, the archive catalogue and versioned formats. Implement small immutable export batches, compression, schema compatibility, checksums, atomic publication and independent replication. Store daily route checkpoints and the necessary baseline/source evidence. Backfill existing stored records first, preserving original collection environment and provenance.

Exit: export counts/identities and deterministic sample hashes reconcile with the database; archived files can be read and replayed independently. Tests cover interruption before/after catalogue publication, duplicate export, late arrivals, malformed data, unavailable archive, checksum mismatch, IPv6, high ASNs and complex paths. Confirm that overwritten historical source values are honestly marked unavailable, not reconstructed from current values.

### Phase 2 — Enable safe retention and resume continuous collection

Introduce time-partitioned immutable history and migration adapters, preserving current route state. Run archive/expiry decisions in dry-run mode first. Local expiry requires published/verified primary objects, verified independent copies, durable deduplication/replay identity and no unresolved reader/checkpoint dependency. It removes a verified local copy, not the twelve-month record.

Replace the fixed 2-GiB test budget with a measured 72-hour recovery-buffer budget plus storm/backlog headroom, under all-writer disk limits. Resume receivers only after the recovery-safe boundary and archive path are established; replay the recorded interruption without inventing continuity. Do not disable sampled collection until coverage parity and unique-observation preservation are demonstrated.

Exit: at least seven continuous days with bounded local storage, successful export/replication, zero unexplained sequence loss and successful gap repair. Exercise archive/replica outages and a capacity pause; no premature expiry, forced in-flight stop or silent loss. Database expiry migration has a tested rollback from archived partitions.

### Phase 3 — Deliver historical investigation and UX

Add twelve-month time selection to the existing 2D/3D/topology investigation context. Support ASN/prefix/path history, route-at-time, before/after comparison, raw evidence drill-down and consistent CSV/JSON exports. Results identify source, viewpoint, time zone, resolution, gaps, coverage and whether evidence was live-captured or backfilled. A month before collection began must display unavailable history.

Use indexed recent data, compact aggregates and archive detail through one API contract. Preserve bucket peaks/counts and loss packet denominators; do not calculate packet-percentile RTT from medians of medians. Longer jobs show progress, scope, estimated scan size, cancellation and partial coverage. Shared URLs retain historical time, observers and selected resources.

Proposed measured targets: recent seven-day scoped requests p95 under 2 seconds; twelve-month scoped aggregate queries p95 under 5 seconds; a bounded archive-detail first page p95 under 30 seconds. Larger route replays run asynchronously with published tested limits. Benchmark under simultaneous ingestion and agreed concurrency; these are acceptance targets, not current capabilities.

Exit: operators reproduce reviewed historical routing and performance investigations, including gaps and withdrawn routes, across hot/archive boundaries without duplicate counts or current metadata leaking into past evidence. Complete rendered desktop/mobile/accessibility review.

### Phase 4 — Build twelve-month analysis

Add 7/30/90-day and seasonal comparisons, time-of-day/day-of-week baselines, change persistence/hysteresis and historical route/latency/loss correlation. Compare stable observer/probe cohorts, show sample counts and coverage changes, and retain contrary evidence. Separate flapping, visibility changes, suspicious origins/leaks, measured degradation and confirmed provider statements.

Store detector/baseline versions and their input-manifest references. Keep what was detected then separate from later reanalysis. Historical RPKI conclusions require the validation material available then; applying today's ROAs to an old route is labeled retrospective validation. Overlapping RIS/RouteViews/IODA lineage does not become independent corroboration. Performance candidates remain scoped to observed endpoints.

Exit: versioned replay scenarios measure detection delay, recovery delay, precision and false positives. Feed outages do not become Internet recovery. Analysis can be recomputed from retained evidence after an algorithm update, with reproducible versions and resolution.

### Phase 5 — Qualify and operate the annual service

Run a thirty-day operational soak and labeled twelve-month-volume tests with concurrent ingestion, year-range queries, source surges and archive interruptions. Test near-full disk, replica lag, corrupt objects, missing manifests, schema upgrades and recovery on another host. Verify twelve-month expiry and checkpoint dependencies using an accelerated test clock, without pretending those tests create real historical observations.

Keep operational backups separate from the analytical archive: propose a seven-day database point-in-time recovery window and four weekly restore points, with a measured backup-space budget. Base backups plus a continuous WAL sequence are required for PostgreSQL PITR; a folder of observation files or logical dumps alone is not that backup. [3]

Proposed operating targets: independent archive-copy lag no more than fifteen minutes in normal operation; operational database recovery point no more than fifteen minutes; live-service restore within one hour and historical catalogue/query restoration within four hours on the agreed hardware. Drill and adjust these targets before promising them publicly. Ingestion gaps before durable commit remain explicit even if a provider later repairs them.

Alert on archive/replica lag, missing partitions, checksums, failed expiry, dedup growth, WAL/temporary storage, oldest/newest queryable times, projected days to full and gaps by observer. Keep at least 30% planned storage headroom and an absolute free-space reserve derived from measured recovery/compaction needs; the existing 20-GiB threshold is not the final universal policy. Expose retained coverage and system-storage health separately from Internet-health findings.

Exit: demonstrated restore and replay, twelve-month retention audit, capacity forecast within an agreed tolerance, runbooks/ownership and UAT acceptance. Promote the exact qualified scope with verified rollback. Existing live-data production authorization persists; this plan does not itself change production. Continue monthly storage/coverage reviews and re-size before each ingestion expansion.

## Historical backfill and release order

RIS publishes snapshots and update archives, which can support routing backfill where the required peer/AFI and intervals actually exist. Qualify availability and integrity before promising a date range. Public source URLs alone do not substitute for our retained, verified copy. Historical measurement, source freshness and authorization context can have different availability and must remain distinguishable. [4]

Deliver a useful slice before the entire analytics programme: archive pilot and history capture; safe local expiry/resumed routing; seven-day history UI; thirty-/ninety-day analysis; then annual-volume qualification. The final user-facing contract remains twelve months of full captured detail plus analysis. No calendar completion estimate is asserted until Phase 0 establishes throughput, storage placement and migration effort.

The next executable increment is Phase 0 plus a non-destructive Phase 1 archive pilot. Expiry, disk allocation and production deployment remain later concrete implementation steps; no permission request is needed merely to complete this plan.

## Primary technical references

1. [PostgreSQL 17 partitioning and constraint limitations](https://www.postgresql.org/docs/17/ddl-partitioning.html)
2. [DuckDB Parquet reading/writing, compression and filter/projection pushdown](https://duckdb.org/docs/lts/data/parquet/overview.html)
3. [PostgreSQL 17 continuous archiving and point-in-time recovery](https://www.postgresql.org/docs/17/continuous-archiving.html)
4. [RIPE RIS MRT snapshots and update archives](https://www.ris.ripe.net/docs/mrt/)
