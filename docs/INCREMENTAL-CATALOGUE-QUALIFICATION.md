# Incremental catalogue qualification

Status: shadow qualification. Public serving remains on the existing full builder until live parity, catch-up, storage and freshness gates pass. Deployment of the shadow is not completion of the catalogue rollout.

The processor atomically captures coalesced observer/prefix dirty keys in source transactions. It takes observer locks before queue locks, matching ingestion, and serializes reference-count writers with an advisory lock. Oldest pending work selects the next observer. Source resets and truncations block publication while bounded reconciliation scans source and derived keys. Independent-connection tests cover new resets arriving during that scan.

An explicit bootstrap copies an MVCC snapshot without locking source writers. Dirty keys remain durable during the copy, including commits concurrent with it. Budget checks run after the route copy, memberships and aggregates; failure rolls back derived state and preserves source evidence. Production installation requires an explicit shadow flag and identity/admission checks. Source DDL uses a 200 ms lock budget. The bounded worker limits batch size and statement duration and checks the derived-storage and free-space reserves.

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
| Concurrent small fixture | 6,700 source changes, queue drained, exact routes and pinned history; artificial lock conflict retried |
| Installer/detach recovery | Busy-source refusal within lock budget; actual service-user process, source progress while detached, full reset recovery and pinned views pass |

The retained large read fixture contains synthetic derived state and is not aligned with its small source fixture. Reset/reseed that qualification database before using it for maintenance correctness tests.

## Current-route index decision

Keep the production indexes. A timestamp BRIN/fillfactor candidate reduced replay WAL and enabled HOT updates but failed the analyzed recency-read gate: 0.201 ms became 38.392 ms on the smaller fixture; the million-route comparison was 0.202 versus 278.602 ms. Repeated write timing also regressed. Lower WAL alone is insufficient for promotion.

## Remaining release gates

Complete the production shadow bootstrap, measure catch-up under live/recovery input, compare source projection and published counts, qualify retained-history growth and reset bursts, and verify API/source freshness under load. Then promote reversibly and retire routine full rebuilds. Preserve fallback generations and unrelated diagnostic UI/API work. Complete private logs, including failed candidates, remain outside the repository.
