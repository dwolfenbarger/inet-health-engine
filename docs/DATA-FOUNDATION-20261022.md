# Public source evidence release — 2026-10-22

This release addresses the data-fidelity audit before expanding global health claims.

## Completed

- PR #5 widens all 12 ASN fields to BIGINT/BIGINT[] and removes the NOC's high-ASN replacement. Production had rejected AS4288000245. Both databases passed real codec tests with all test rows rolled back; production scheduled RIS cycles resumed successfully.
- Local Routinator 0.15.2 validates production RIR trust anchors and binds HTTP/RTR only to 127.0.0.1. An in-process covering-prefix index consumes its verified ROA snapshot. Origin validation preserves competing/matching VRPs, AS0, maximum lengths, invalid reason, and generation time. Missing, malformed, failed or >20-minute-old snapshots never become not-found. Recent observed prefix/origin pairs are revisited oldest first, at most 50,000 per cycle.
- Atlas reads existing public K-root ping IPv4 (1001), ping IPv6 (2001), and DNS (10001) measurements. It stores latest evidence per measurement/probe, excludes stale (>1 hour) results, counts malformed exclusions, retains total loss and failed DNS evidence, and never puts a source IP in an ASN field. It does not create paid or new active measurements.
- IODA uses the current Georgia Tech event endpoint, the native country/ASN format, explicit time range and pagination (up to 1,000 events per entity type per cycle). Provider scores remain provider scores; routing lineage overlaps RIS/RouteViews. Empty events do not imply recovery. Stored events are evidence, not an assertion they remain active.
- `/api/v1/noc/sources` reports global source collection state, successful collection time, latest evidence time, count, scope, limitations and lineage. Counts are independent of investigation filters. `/api/v1/noc/evidence/{source}` offers bounded raw evidence and optional ASN/family filters.
- NOC source coverage panel distinguishes partial, stale, failed, unconfigured and unavailable sources. Route evidence separates current fresh local validation from the RPKI value recorded at ingestion.
- Provider evidence and status commit atomically. Late older evidence cannot replace newer evidence, and a collection error preserves prior timestamps while making failure explicit. Source-specific advisory locks prevent overlapping runs. Runtime collectors no longer attempt privileged startup DDL.

## Validation

- 25 Python regressions; frontend TypeScript/Vite build, targeted ESLint and six existing preview-state tests.
- Real PostgreSQL regression verifies ordering, full ASN values, stable identity and preserved success timestamps on errors; every test record rolls back.
- Live UAT: 50,000 local RPKI validations in ~38 seconds, ~850 MB peak staging process memory. Subsequent scheduled work grew current evidence to 100,000 distinct pairs. Atlas collected ~35,900 recent results across IPv4, IPv6 and DNS. IODA endpoint returned a successful empty current window; no health conclusion was inferred.
- HTTP checks exercise source state, raw evidence, IPv4/IPv6 snapshot requests, high ASN filters, empty scopes, current RPKI proof fields, unknown sources and invalid bounds. Health scores remain null.
- Visual browser review is unavailable in this session: the connected browser is disconnected; cloud fallback reports ERR_CERT_COMMON_NAME_INVALID for the private UAT IP. No certificate warning was bypassed. Build and functional HTTP checks pass; a rendered UI review remains outstanding.

## Deployment

Apply `docker/migrations/20261022-source-evidence.sql` as the database owner before the new API/collectors. Fresh database initialization includes the same schema. Install the official signed NLnet Labs Routinator package with `scripts/install-routinator.py`; existing configuration is never overwritten silently. Wait for a verified snapshot. `ROUTINATOR_URL` defaults to `http://127.0.0.1:8323`.

Use the established native backup, checksum and drained-worker promotion. Stop beat, prove active/reserved/scheduled work is zero, then restart workers. No queue purge or forced task termination. Reverting application code to the prior ASN-fixed release is compatible with the additive source tables; do not narrow ASN types.

## Remaining scope and known limits

- RIS is still a capped intermittent sample from five configured collectors: 50,000 updates per 60-second window, scheduled every 90 seconds. This is not a complete RIB or continuous routing state. Peer IP/session persistence, broader geographic viewpoints, continuous collection, RIB bootstrap, reconnect gap accounting and MRT reconciliation are next routing work.
- RouteViews ingestion is not connected. A parser was verified available in UAT for future MRT work; metadata discovery is not counted as live routing coverage. Observer independence must account for overlapping peers.
- Cloudflare Radar has no configured token or granted Vault credential for this requester. It is explicitly unconfigured. The prior unverified adapter and its score-to-confidence inflation are no longer used by scheduled traffic collection. A validated credentialed adapter is pending.
- M-Lab/HERMES baselines and targeted Globalping diagnostics remain pending. Probe-to-ASN/country attribution, more Atlas targets, historical baselines, event correlation and congestion attribution require further work.
- UAT is PostgreSQL without Timescale; production is TimescaleDB. The ASN migration supports both and refuses compressed ASN chunks.
- No source combination establishes complete global Internet health. No global score, physical-path claim or confirmed hijack is inferred from missing data or a single provider flag.
