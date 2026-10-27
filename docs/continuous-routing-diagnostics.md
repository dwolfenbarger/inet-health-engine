# Continuous routing diagnostics

The live issue view and ASN/prefix investigations use an independent, checkpointed consumer of the existing selected-peer RIS journal. It records actual state transitions with the previous and next AS paths, origin, peer, source event time, receipt time, journal identity and continuity epoch. It does not add a feed or infer global health from partial observations.

## Operator workflow

Open an ASN or IPv4/IPv6 prefix/IP investigation. The Routing history panel shares the issue window, family and collector filters. ASN matching includes recorded origin, prior origin, transit and collector-peer involvement. Prefix searches include more-specific routes; IP searches include covering routes. Inspect an observation to see exact before/after state, surrounding retained changes and raw evidence. A withdrawal-surge detail enumerates the recorded prefix/path set, marks lower bounds, and identifies the trigger prefix separately.

The issue list labels continuous evidence separately from older sampled candidates and measured performance evidence. Its coverage panel exposes each selected peer's intake freshness/session state, analysis heartbeat, event time, backlog, baseline epoch, late-change count, source gaps and errors. These sources share lineage and may overlap; two detector modes are not independent confirmation. A successful consumer state is not a verdict that the Internet or an ASN is healthy.

## Detection and continuity

- A first prefix observation after activation, a source/sequence/session gap or state expiry establishes a baseline rather than fabricating a change. Installation starts at each committed journal head.
- Flapping requires at least 25 presence or announced-path transitions in 300 seconds. Identical reannouncements do not count.
- An origin-change candidate requires a prior announced origin baseline at least 300 seconds old. Legitimate multi-origin routing and operator changes can trigger it; authorization and attribution require separate evidence.
- A withdrawal surge requires at least 200 distinct previously known-present prefixes for a peer/origin in 60 seconds. Bounded state retains up to 256 prefix/path entries and explicitly marks lower bounds.
- Transactional evidence/state/checkpoint writes and deterministic event identities make retry and restart idempotent. Late prefix changes cannot replace newer state. Corrupt identities/times or future input produce an explicit error without advancing the checkpoint.
- Source gaps remain unobserved in diagnostic history even if a separate MRT process repairs the current RIB. A new baseline cannot reconstruct missed transitions.

AS paths represent BGP control-plane observations. They are not traceroutes, latency measurements, proof of reachability, proof of congestion or confirmed hijacks. Real viewpoint-selected traceroute, incident correlation and broader global coverage remain separate roadmap work.

## Bounds and deployment

Apply `20261026-routing-diagnostics.sql`, then `20261027-diagnostic-lock-isolation.sql`, before enabling the worker. The follow-up removes only this component's foreign keys to mutable collector observer rows: PostgreSQL's foreign-key KEY SHARE check otherwise conflicts with the collector's FOR UPDATE. Observer identities are validated through decoded committed journal input; retained evidence survives observer metadata lifecycle changes. Never add a lock on a collector observer row to this consumer.

Use `scripts/install-diagnostic-index.py` to build the observer/sequence index on each journal partition concurrently, serially, and attach the valid parent. Its owned manifest permits repair only of an exact, previously owned invalid index. Future partition inheritance must be verified. Run the consumer as the native service user using the native venv and EnvironmentFiles, with `python -m collectors.routing_diagnostic_worker`; `--observer` scopes a canary. One advisory lock prevents concurrent owners.

The runtime uses 128 journal frames and at most 16,384 decoded prefix changes per transaction, with local 200ms lock and 5s statement budgets. Oversize frames are refused visibly with input retained. Default detail retention is up to 24 hours, within an 8 GiB component allocation cap and a 35 GiB root reserve. A visible capacity pause preserves input; it is not a promise of unlimited catch-up or a guaranteed full-day window under every workload. Retention cleanup preserves an extra five minutes for active investigations. Raw intake and source checkpoints have their own retention contract.

History pages fence committed per-observer heads and expire five minutes after the first page. Later pages cannot extend that lifetime. A changed filter is rejected; expiry or reduced retention requires refresh. Increased retention cannot widen an existing snapshot. This avoids admitting lower sequence IDs committed after a page snapshot.

Stage API modules using a temporary localhost-only FastAPI app mounting the NOC router, without the native full app's Redis/WebSocket subscribers or background warmers. Preserve the installed catalogue overrides and incremental feature flag. Qualify before an idle-gated, graceful API handoff. Publish fingerprinted static assets first, HTML last, and retain old assets for cached clients. Do not restart PostgreSQL, intake, recovery or accepted Celery work for this component. Future PostgreSQL drain procedures must include the diagnostic consumer among the database clients.

Rollback restores only owned API/UI files through hash gates and gracefully hands the API back; the diagnostic component can be stopped/disabled separately without deleting journal input, cursor state or evidence. A running PID alone does not prove service-user cold startup or endpoint correctness.

## Qualification

`tests/test_routing_diagnostics.py`, `tests/test_diagnostic_status.py` and existing issue contracts cover detector semantics and visible failure states. `scripts/test-continuous-diagnostics-db.py` is guarded to an isolated development host/database and exercises real PostgreSQL and ASGI requests: replay, duplicate handling, failure before checkpoint, retry, connection restart, source/sequence/session resets, actual held-observer-row concurrency, batch limits, malformed/future input, IPv4/IPv6 withdrawal sets, transit/prior-origin matching, delayed-commit pinned pagination, expiry, retention grace and filter boundaries. It is not a production replay tool.

`qualify-diagnostics.py` issues read-only live requests for history pages, prefix/IP scope, exact detail, empty/invalid cases and issue integration. Its output contains real examples and belongs only in private evidence. Native service cold start/restart and capacity pause/resume require separate dedicated-database qualification. Frontend compilation and changed-file lint establish build acceptance; they do not establish rendered interaction, mobile layout, accessibility or public-load acceptance.

Measure whole guest/host-device I/O, WAL/checkpoints, memory pressure, endpoint latency, observer/recovery/catalogue progress and this component's CPU/allocation before and after. Use raw endpoint counter differences over actual durations. Source traffic, recovery, vacuum and other host workloads differ; observed changes are not matched-workload causal savings or per-spindle measurements. Preserve anomalous host timing counters and failed controller attempts instead of subtracting or hiding them. Keep all production telemetry private.
