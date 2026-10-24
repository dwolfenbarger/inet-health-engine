# Operational issue investigation — Phase 1

The charter is an ISP/NOC troubleshooting tool: identify what changed, where and when; investigate affected ASNs, prefixes and observed paths; distinguish evidence from inferred impact. Completion requires operator workflows on live evidence, not collection counts.

## Delivered behavior

- The default workspace leads with routing and measured-path candidates, independently of maintained RIB browsing.
- Filter 15 minutes, one hour, six hours or 24 hours; filter category, ASN involvement, prefix/IP, family and collector. Routing ASN involvement includes expected/victim origin and recorded path/peer appearances.
- Immutable five-minute list snapshots paginate all matching groups. Counts describe groups; detector reports, transition counts and distinct collector/peer viewpoints remain separate.
- Pin an exact routing observation by UUID and timestamp. Inspect detector facts, recorded AS path and surrounding sampled updates; navigate to its prefix, observed/expected origin or recorded intermediate ASN without losing evidence.
- Measurement details expose genuine named probe/destination evidence and source links with a separate measurement clock. Prefix/collector filters exclude mesh measurements because mapping is not established.
- Failure, missing evidence, stale measurements, expired snapshots and incomplete detection never become an all-clear. No route, RTT, recovery or confirmed hijack is fabricated.

## Scope and remaining phases

1. **Issue visibility and evidence drilldowns:** this release. Qualify API, desktop/mobile and production workflows before recording acceptance.
2. **Continuous prefix/ASN diagnostics:** feed qualified detectors from continuous routing state; persist transition history, checkpoint state, handle replay/duplicates/gaps, establish baselines and detector clocks. Current candidates still come from sampled RIS detection.
3. **Path investigation:** source/destination ASN selection, ordered transit/intermediate nodes, viewpoint comparison, temporal path changes and explicit visibility gaps.
4. **Measured troubleshooting:** actual ping/traceroute measurements from suitable source probes, hop-to-AS mapping and comparison over time; preserve unknown hops and asymmetric visibility. The legacy synthetic traceroute implementation must not be reused.
5. **Impact correlation:** evidence-backed regional, prefix and ASN impact, confidence and scope, timeline/cause hypotheses, and linked 2D/3D/topology views.
6. **History and ISP workflows:** watchlists, alerts, saved/shared investigations and 12-month analysis. Respect 150 GiB local and 350 GiB self-imposed NAS budget, full bulk retention development-only, `/NAS1/storage/ai/ihealth_storage`; backup expansion remains on hold.
7. **Sustained operations:** detector/source health, query and public-load budgets, accessibility, retention validation and operational runbooks.

Each phase must demonstrate a complete investigation using real evidence or a clearly labeled controlled replay. New feeds and storage growth require a specific diagnostic benefit. Phase 1 does not establish global coverage, continuous anomaly detection, confirmed incidents or inferred congestion location.

## Validation

`tests/test_issues.py` covers resource scoping, expected/transit/peer involvement, pinned pagination, explicit failures, stale measurement semantics and exact observation detail without invented prior paths. `scripts/qualify-issues.py` walks every live issue page and tests real flap/origin-change/withdrawal candidates, victim searches, prefix drilldowns and available measured changes. Set `INET_ISSUES_QA_URL` to the target `/api/v1/noc` base.

## Validation result

Release `6022ee173f58d94db28942027d94668c8a92d597` passed98 Python regressions in the native Python3.13 environment, the frontend build and changed-file ESLint. Live workflow checks covered all issue pages, exact observation details, expected/transit ASN searches, prefix drilldowns, measured evidence, invalid/expired scopes and desktop/mobile layout. Public-load and full accessibility qualification remain open.

Detailed operational reports, timestamps, observation identifiers, screenshots and deployment/rollback records are retained privately in the project ledger and release workspace. They are not included in this public repository.

Phase1 restores issue-to-evidence investigation. Broader charter acceptance remains open: continuous intake does not yet feed these detectors; prefix event history, event-time validation, confirmed incident correlation, recovery detection, impact inference and native probe-selected traceroute investigation remain unfinished. Source links to Atlas evidence do not replace Phase4. Serving catalogue refresh delays and cold queries remain operational constraints. The legacy globe-summary timeout and synthetic traceroute code are not repaired or exposed by this release.
