# ASN usability implementation checkpoint — 2026-10-24 UTC

Implementation remains incomplete and is NOT deployed to production. Controlling acceptance checklist: PRODUCTION-USABILITY-PRIORITY.md. Do not claim that this candidate meets the charter or production acceptance.

## Access interruption

Vengeance MCP returned HTTP403 `audit_unavailable`, next_action `stop_and_review_access`, on the attempted integrity-checked write of C:/ai/inet-global-20261022/asn-fix-writer.py. Stopped remote operations as instructed. Restore connector audit service/access before continuing. No alternate access route was attempted.

## Durable remote work

C:/ai/inet-health-routing-20261023, branch openai/continuous-routing, base 06ef67d9d8ebe0d090b5039bc74f9c4094b012c5, has uncommitted initial catalogue migration/builder/API, directory UI, route pagination and tests. Draft PR7 exists but these changes have not been committed/pushed. Production remains c8c108fb9510e986e7d686ca6955b0c05705c48f.

Applied additive inet_catalog schema ONLY to UAT CT554. The one-shot inet-asn-build-qualification.service was active at the last successful check, with only its start line in the journal. Its final result is UNKNOWN. Candidate directory /opt/inet-asn-candidate. Do not start another builder until checking its state and PostgreSQL activity. No recurring catalogue timer installed. Existing live collection was not intentionally stopped.

## Local-only latest fixes

The current frontend/src/live/LiveNOC.tsx removes two narrowed-type-invalid ternaries in the sampled origin panel and trims extra EOF blank line. ASNInventory.tsx adds request-key gating to avoid showing previous filters' results while loading, and labels detail capture time. These last fixes did NOT transfer because access failed. Earlier remote build failed TS2367 at LiveNOC line133; it must be rerun after transferring fixes. Python compileall passes locally. Native dependency tests, full frontend build, API integration, rendered QA and production acceptance remain pending.

## Resume checklist

1. Inspect UAT builder journal, activity, duration, free space and actual derived relation size. Initial builder statement timeout is 900 seconds and temp limit4GiB; verify transaction rollback if failed. Copy/index of millions of routes needs qualification.
2. Transfer latest local files with scripts/pack-transfer.py and files_write SHA gate. Rebuild frontend and lint changed files. Candidate API/tests/QA server also need recopy to UAT; scripts/serve-catalogue-qa.py binds127.0.0.1:8011 and loads private native .env internally.
3. Fix catalogue storage budgeting before production: current guard checks existing12GiB but does not reserve full incoming generation. Keep last2 published versions, guard root reserve35GiB, and allow safe expiry of derived copies without changing source evidence.
4. Ensure catalogue expiry during a request cannot silently return empty200; use a consistent request transaction or final generation recheck. Improve filtered counts with existing per-ASN aggregates/caching and benchmark heavy transit lookups. Ensure numeric path ASNs are within1..4294967295.
5. Test all cursor contracts, role filters, longest-prefix search, metadata lookup/failure, snapshot expiry and directory totals. Exercise ten real ASNs, beyond200 routes, IPv6 and four-byte identities. Benchmark while collection runs. Current test file has four contract tests only.
6. Qualify/install bounded periodic builder, then real UAT rendered workflows. Preserve source/filter context across navigation and label map geography honestly. Full adjacency/detail and visual integration acceptance still open.
7. Commit/push exact tested release, use guarded deployment with worker drain and rollback; verify same workflows in production. Update ledger and controlling checklist with actual results. No backups or new feeds requested.

Remote ledger could not be updated after access interruption. This checkpoint is local and must be reconciled into the repository and project ledger when access returns.

## Access restored and qualification resumed

On David's Retry, ledger, shell and file writes succeeded. Connected Chrome browser also opens UAT correctly; prior extension/certificate blocker no longer applies to this browser. The first build timed out at900s and rolled back completely (no generations or partitions). Revised bulk load copied3,771,205 eligible rows in282.18s; indexing/aggregation qualification still pending at this checkpoint. Current candidate frontend builds and changed-file lint passes. All85Python regressions pass with complete candidate scripts present. None of this constitutes production acceptance. Latest local fixes transferred to Windows; running UAT build uses the second builder revision, prior to added post-copy/post-index budget checks. Candidate loopback API8011 active underinet-catalogue-qa.
