# Current route-state write consolidation

The collector previously updated the same current route multiple times inside a
single transaction. It now selects the winning row for each observer and prefix
before applying current-state UPSERTs. Selection follows the existing SQL ordering:
epoch, event timestamp, rank, then event order; the first equal row wins.

Every accepted input frame still enters the durable journal. Deduplication,
sequence numbers, evidence references, session resets, gaps and checkpoints keep
their existing semantics. Flaps and intermediate origin/path changes remain
available to journal consumers. This change does not reduce collection or
extend the batching interval.

## Validation

- Six focused tests cover ordering, observer/prefix isolation, randomized
  equivalence against existing state and journal preservation through the actual
  commit function. Thirty announce/withdraw transitions plus a duplicate produce
  thirty journal records and one final current-state write.
- The complete development suite passed: 112 tests.
- A rollback-only PostgreSQL replay used 2,048 captured frames from four observers,
  32 batches and 4,134 route operations. The test relation used the production
  current-route indexes. Raw input and operational reports remain private.
- All 2,616 final routes matched exactly, including ordering and evidence fields.

| Repeated replay | Original | Consolidated | Reduction |
|---|---:|---:|---:|
| Statements / row versions | 4,134 | 3,784 | 8.47% |
| Statement WAL bytes | 4,996,083 | 4,505,969 | 9.81% |
| Shared blocks dirtied | 345 | 310 | 10.14% |
| Elapsed seconds | 2.980 | 2.673 | 10.30% |

The first pair also reduced WAL (5,190,005 to 4,590,592 bytes). Timing and buffer
counts are sensitive to cache state and instrumentation. These are current-state
replay results, not a claim about total physical spindle IOPS. Journal writes,
recovery, catalogue builds, checkpoints and vacuum remain additional workloads.

## Deployment and rollback

Deploy only the routing-store module to the dedicated stream/recovery runtime.
Gracefully stop both workers at a committed boundary, verify the installed and
candidate hashes, retain the original module, then start both workers. Confirm
all configured live observers advance and recovery continues. Do not mix this
change with unfinished diagnostic API/UI work.

Rollback uses the same graceful boundary and restores the saved module. Any
source interruption must remain visible as an observation gap; do not describe a
worker restart as uninterrupted collection.

## Measurement limitations

Live comparisons require the same active guests and independent measurements of
input volume, recovery and catalogue activity. A short before/after window cannot
prove causation when these workloads differ. The controlled identical-input
replay is the evidence for the optimization's write reduction.

## Production observation

The dedicated writer was deployed after a clean committed-boundary stop. All
four configured live RIS observers advanced afterward; archive recovery resumed.
The native application and pending diagnostic UI/API were not deployed.

Two approximately one-minute windows, with the UAT guest stopped in both, showed:

| Mixed live workload | Before | After |
|---|---:|---:|
| Production guest IOPS | 2,452 | 821 |
| Host device IOPS | 2,581 | 1,161 |
| Production guest write MiB/s | 19.31 | 10.10 |
| Accepted live route changes | 5,791 | 6,558 |
| Current-state updates including recovery | 10,753 | 21,749 |
| PostgreSQL WAL bytes, all workloads | 62,412,215 | 156,777,154 |

This is **not a causal 67% IOPS reduction**. The baseline included a catalogue
build; recovery, background collectors and vacuum differed afterward. Aggregate
WAL actually increased. The defensible benefit remains the identical-input
replay's 8.47% fewer row versions and 9.81% less statement WAL. Host-device IOPS
are not per-spindle measurements, and device merging differs from guest counters.

A second post-change minute measured 543 guest IOPS, 839 host-device IOPS and
9.59 MiB/s guest writes, with 5,154 accepted live route changes. All four live
observers continued advancing. This window also had a different recovery mix
(18,173 route-state updates and 106,760,604 aggregate WAL bytes), reinforcing the
need to use the controlled replay for causal attribution.
