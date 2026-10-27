# Incremental catalogue qualification

Status: the incremental serving release has passed deployment-specific source parity and HTTP acceptance. The selected deployment now uses incremental serving with routine full rebuilds retired and legacy pinned snapshots retained. Operational measurements, release manifests and source telemetry remain in the private ledger/evidence; this document records design and synthetic qualification. Sustained burst/freshness and retained-history checks continue to determine operational acceptance.

The processor atomically captures coalesced observer/prefix dirty keys in source transactions. It takes observer locks before queue locks, matching ingestion, and serializes reference-count writers with an advisory lock. Oldest pending work selects the next observer and oldest queued keys within it. Ready publication is attempted at the just-drained batch boundary, preserving the strict atomic empty-queue/reset gate; waiting for an extra empty maintenance cycle had starved publication under continuous intake. Source resets and truncations block publication while bounded reconciliation scans source and derived keys. Independent-connection tests cover new resets arriving during that scan.

An explicit bootstrap copies an MVCC snapshot without locking source writers. Dirty keys remain durable during the copy, including commits concurrent with it. The initial bulk copy defers derived indexes and version triggers until loading finishes, restores them before commit, and refuses any previously initialized/published shadow. Verified-empty derived tables may be truncated to reclaim pages left by an interrupted initial attempt. Source queues and journal rows are preserved. Budget checks run after the route copy, memberships, aggregates and rebuilt indexes; failure rolls back derived state and preserves source evidence. Production installation requires an explicit shadow flag and identity/admission checks. Source DDL uses a 200 ms lock budget. The bounded worker limits batch size and statement duration and checks the derived-storage and free-space reserves.

Reference counts maintain distinct prefix/observer totals, ASN roles and global facets. Ordered transit and raw AS_SET semantics match the existing builder. Only changed memberships are written. Changed rows retain version history for pinned navigation. Cleanup expires metadata before reclaiming its history, preserves tokens remaining after a bounded cleanup, and retains the last ready publication when processing stalls. Raw diagnostic journal evidence is preserved.

The default-off API adapter keeps legacy generation IDs and uses a separate range for incremental versions. It reports capture age, processing lag, reset status, incomplete publications and explicit expiry. Route pages bound each current/history branch before merging. ASN membership pages use bounded parameterized route-key lookups; allowing a generic sort/hash join had caused full-inventory scans and was rejected.

## Qualification evidence

All figures below are synthetic DEV measurements, not production or per-spindle IOPS savings.

| Qualification | Result |
|---|---|
| Randomized state/counter checks | 560 mutations; exact routes, memberships, ASN counts, global counts and facets |
| Bootstrap and reset lifecycle | Concurrent source commits, interruption/retry, reset revision restart, deleted keys, old pinned views pass |
| Actual HTTP and SQL application role | 94 checks pass: ASN roles, IPv4/IPv6, longest prefix, filters, concurrent pagination, stale/reset status, expiry, legacy fallback |
| Existing native regressions | 112 pass |
| Indexed full reference versus incremental | 1,000,001 routes, 400 changes: full 839,726,960 cluster WAL bytes / 81.231 s; incremental 3,295,000 / 0.347 s |
| Full-size serving fixture | 3.8 million routes and memberships: global page 2.646 ms, origin 2.584 ms, ASN page 2.896 ms, next page 2.940 ms, membership route join 4.136 ms |
| Capture overhead | 1,000 source updates: repeated disabled 0.0859 s versus enabled 0.1664 s; dirty queue exact; WAL counters are cluster-wide |
| Concurrent small fixture | 7,200 source changes in the latest run, queue drained, exact routes and pinned history; artificial lock conflict retried |
| Installer/detach recovery | Busy-source refusal within lock budget; actual service-user process, source progress while detached, full reset recovery and pinned views pass |

The retained large read fixture contains synthetic derived state and is not aligned with its small source fixture. Reset/reseed that qualification database before using it for maintenance correctness tests.

## Current-route index decision

Keep the production indexes. A timestamp BRIN/fillfactor candidate reduced replay WAL and enabled HOT updates but failed the analyzed recency-read gate: 0.201 ms became 38.392 ms on the smaller fixture; the million-route comparison was 0.202 versus 278.602 ms. Repeated write timing also regressed. Lower WAL alone is insufficient for promotion.

## Remaining release gates

Preserve fallback generations, source continuity and raw evidence. Continue whole-cycle measurements under live/recovery bursts, verify old tokens expire before reclaiming their history, and qualify longer retained-history growth. Pinned-page consistency, explicit source gaps and capture/processing-age labels remain mandatory. Do not equate catalogue serving acceptance with continuous detector, traceroute or global-impact product acceptance. Complete private logs, including failed candidates, remain outside the repository.


## Historical summary experiments

The existing PostgreSQL operational database remains the primary store. A separate historical summary tier is a candidate for long-range analytical queries; it does not replace raw event order, paths or diagnostic evidence.

Two synthetic hourly-count experiments used the same million-row input on one isolated PostgreSQL instance with 2 CPU and 2 GiB memory. Both retain unknown origin/collector values as NULL and verify late event time through a new immutable segment. These are projected count summaries, not production capacity or physical IOPS measurements.

| Synthetic summary method | Cluster WAL bytes | Client elapsed | Summary allocation |
|---|---:|---:|---:|
| 101 calls, 10k-row batches | 169,410,144 | 16.676 s | 18,219,008 bytes |
| One verified sealed segment, concurrent duplicate worker | 10,203,656 | 0.413 s | 5,455,872 bytes |

Both produce the same 40,002 groups for 1,000,003 observations. Client time includes Docker/psql overhead, and cluster WAL may include background work. The whole-segment arm bounds admission to one million projected rows and a 30-second statement budget. It requires an actual segment adapter and real source/cardinality, spill, recovery and storage qualification before production use.

The tests cover rollback of contribution plus checkpoint/ledger, duplicate replay, checksum mismatch, incomplete ordinal coverage, two independent workers contributing once, sealed receipt/manifest mutation refusal, NULL values and exact reconciliation. An identity sequence on an OPEN live table is not a safe commit watermark: allocation and commit order can differ. Consume only immutable, verified sealed segments or use a separately proven live change protocol. Keep source namespace, provider identity/dedup and continuity evidence intact.
