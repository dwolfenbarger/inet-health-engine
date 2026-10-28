# Fourteen-day routing investigations

The routing history API and NOC history panel support a fourteen-day lookback
for the selected continuous RIS peers. This preserves observed announcements,
withdrawals, origin/path changes, detector candidates and continuity metadata.
It does not add viewpoints or recreate evidence lost before activation.
Candidates remain unconfirmed; source gaps remain unknown even when MRT recovery
repairs a current routing table. BGP paths are not measured traceroutes.

## Investigation

Select an ASN, prefix or IP, then choose the history window independently of the
current issue window. Set **Window end (UTC)** to investigate an earlier incident.
The shared family and collector filters still apply. Inspect an observation for
its exact before/after path, timestamps and raw evidence. Overlapping source
gaps are displayed separately. An empty result is not a healthy verdict.

`GET /api/v1/noc/diagnostics/history` accepts `minutes` through `20160` and an
optional timezone-aware `until`. Future or naive end times are rejected. The
response distinguishes requested and effective periods, clipped history and
wholly out-of-retention periods. Existing API filters retain their meanings:
prefix searches include more specifics; IP searches include covering prefixes;
ASN/resource matches in withdrawal aggregates preserve recorded path correlation.

Pagination pins committed observer heads and the first capture time. Its
five-minute absolute expiry never extends on subsequent pages. Filter/window
changes require a new investigation. Expired pages return HTTP 409. Historic
source-gap repair status is current and is not part of the pinned change snapshot.
The global issue overview remains a recent-window view; historical diagnosis
uses the history panel.

## Storage and lifecycle

The production policy is 336 hours, a 192 GiB physical diagnostic allocation cap and
a 64 GiB free root-filesystem reserve. CT553 has a 400 GiB local thin-provisioned
root filesystem. PostgreSQL stays local. Existing development-only bulk NAS
retention is separate and unchanged. These values are capacity controls, not a
guarantee against arbitrary traffic growth or source failures.

The initial adoption attaches the existing table as `rde14_bootstrap`, preserving
its relation, IDs, UUIDs, sequence, timestamps and payloads without copying
history. New evidence uses daily UTC partitions. A GiST inet index supports
covering-prefix/more-specific lookup; history searches separate ordinary prefix
rows from aggregate withdrawal JSON so that the network predicate can use its
index. A separate narrow time index selects withdrawal-surge aggregates without scanning other candidate rows. Every index and TOAST allocation counts against the cap.

The `inet-diagnostic-retention.timer` runs the owned maintenance script every
15 minutes as postgres. It precreates three future days and drops only registered
children whose upper bound is older than 336 hours plus the 300-second page grace.
The daily boundaries and initial bootstrap can retain extra physical history;
public queries still clamp to the configured lookback. Out-of-range late rows
are removed in bounded batches. Unknown children, lock conflicts and statement
timeouts fail explicitly; no destructive inference is made. The API reports
maintenance errors or a check older than an hour as degraded.

The worker recursively counts leaf tables, indexes and TOAST plus diagnostic
state/cursors. At the cap or reserve boundary, analysis pauses explicitly while
source intake continues. This preserves bounded safety, not unlimited catch-up.
Track actual growth, partition expiry, peer cursors and physical thin-pool
headroom; increase capacity before the pause boundary if required to sustain
the target. No packet/raw-provider retention promise is implied.

## Recovery and validation

`scripts/diagnostic-retention.py` provides prepare, cutover, maintain and rollback.
It is host/database guarded, uses an advisory ownership lock and only removes
registered exact children. Cutover and rollback require the diagnostic writer
to be drained. The root maintenance service does not restart PostgreSQL or
source collectors and changes no memory, compression, vacuum or WAL tuning.

The no-copy rollback is rehearsed only before the initial bootstrap upper bound
and while every later partition is empty. It refuses to discard later evidence.
After that boundary, rollback needs a separately reviewed merge; do not run it
blindly. Restore matching API/worker/static code with the schema, using owned
hash gates and graceful drains. Preserve failure journals and private evidence.

Qualification includes real PostgreSQL/ASGI replay, a 13-day-old synthetic
incident with exact paths/times, committed-head pagination across a delayed
transaction, expiry/window mismatch, mixed resource filters, storage accounting,
adoption/rollback identity, active-page grace and physical partition release.
Live candidate/public API and asset checks supplement these tests. Browser
rendered/mobile/accessibility acceptance remains pending when the browser is
unavailable. A full fourteen-day production window must accumulate forward;
synthetic replay does not establish fourteen days of actual source coverage.
