# INET Health: Global NOC product and UI/UX roadmap
Version: 1.6 | Updated: 2026-10-23 | Prepared by OpenAI Codex

## Charter and planning status

David confirmed the goal: a visual replacement for routine BGP looking-glass use and a global Internet health monitor for NOCs and public ISP use. Both 2D and 3D views are required. Operators must quickly understand global conditions and investigate ASNs, prefixes and paths for routing instability, suspected hijacking, congestion and other observable degradation.

This document fulfills the requested phased plan. Scope direction is accepted; phase details, performance targets and suggested defaults below are proposed implementation criteria. Creating this plan does not deploy software, merge code, enroll providers, spend measurement credits or publish the service.

## Current implementation status — 2026-10-23

Production is the source-evidence release (PR #6/master19c2923). The expanded UAT canary in draft PR #7 adds maintained peer/session route views, MRT recovery, IP longest-prefix lookup, five configured routing observers, a regional coverage-gap panel and 28 public Atlas mesh directions across seven anchors. Live mesh collection, target/probe identity checks, 25-hour latency/loss baselines and qualified change candidates are implemented; this is not the full Phase4/5 incident engine.

New York and Paris now have audited 08:00 UTC snapshots and completed archive catch-up. Singapore and Nairobi retain audited baselines. Sao Paulo completed its audited 1,091,211-prefix snapshot and strict archive catch-up. All five now have audited baselines. At the 2026-10-23 19:13 UTC status check, the four RIS receivers were inactive with the raw journal at the 2-GiB safety cap; interruption gaps remain visible. Recoverable retention/archival is now an immediate operating blocker. There is no global health score or defensible global coverage percentage. Independent RIB reconciliation, both address families per region, more diverse measurement endpoints, storage/retention policy, sustained failure testing, rendered UX review and public release controls remain open.

Exact application commits, deployment artifacts, source inventories, tests and current bootstrap evidence: docs/CONTINUOUS-ROUTING-20261023.md and docs/GLOBAL-COVERAGE-20261023.md. Earlier starting-point findings below are historical, not the current production state.

## Product principles

- One investigation, multiple synchronized views: 2D geography, 3D globe and logical AS topology share selection, filters, viewpoints and time.
- Visual overview and technical depth: retain raw observations and route attributes alongside plain-language summaries.
- Every finding identifies its source, viewpoint, observed time, freshness, coverage and confidence.
- Unknown, unavailable and stale are first-class states. No data must never improve a score.
- Separate measured performance, observed routing, inferred cause and estimated geography.
- A public view should make the Internet understandable; an ISP workspace should make its own impact actionable.
- Preserve the existing React/TypeScript implementation and useful globe assets. Add modular views and shared contracts rather than replacing the whole stack.
- Implement public-target validation, quotas, private-data boundaries and observability as their features arrive, not only at final release.

## Initial review starting point — 2026-10-22 (historical)

- Existing source has a 3D globe, event rail/feed, ASN drawer, path/baseline/community panels, mobile shell, Zustand store and TanStack Query.
- Deployed-source snapshot reviewed: recovery/prod-baseline-20261022 at bd87a80; GitHub master remains c3d990a as of this review. The deployed frontend package declares React 19, Zustand 5 and Three.js, despite older README claims.
- Collector loop-lifetime fix is prepared in draft PR1, not deployed: https://github.com/dwolfenbarger/inet-health-engine/pull/1.
- Observed PROD issues: stale BGP ingest, misleading score100/healthy labels and unpublished code/schema provenance gaps. These observations are from the earlier same-day review, not a new live health check.
- App.tsx updates the stored health score only when a new score is non-null, so unavailable results can leave an old value displayed. The globe stays mounted when hidden; render/resource suspension must be verified.
- Existing activeView mixes workspace navigation with visualization selection; selected ASN/path state lacks a common time/viewpoint/URL contract.
- The existing fixed event rail and drawer positioning need responsive redesign. WebSocket reconnect timers need explicit teardown/reconnect lifecycle verification.
- Current source inspection is not a completed visual/browser audit or feature completeness audit.

## User journeys and screen inventory

| Screen/workspace | Primary question | Essential content |
| --- | --- | --- |
| Global Overview | Where are conditions changing? | 2D/3D switch, dimensioned health summary, ranked incidents, affected entities, coverage and time |
| ASN | How is this network behaving and who sees a problem? | Prefixes, routing changes, observed relationships, performance by viewpoint, incidents and evidence |
| Prefix/IP | How is this resource announced and reachable? | Covering/more-specific routes, origins, paths, RPKI, visibility and diagnostics |
| Compare Routes | Why do viewpoints disagree? | Side-by-side routes, attribute/path diffs, observer identity and historical comparison |
| Incident | What happened, what is affected, and what supports it? | Summary, lifecycle, impact, evidence, competing explanations and timeline |
| Diagnostics | How does this destination perform from selected sources? | Probe selection, test progress, comparable results, failure states and measurement budget |
| My Network | What matters to my ASN, upstreams and customers? | Watchlists, saved investigations, scoped incidents and alert preferences |
| Coverage | Where is the platform able to observe? | Healthy/stale feeds, collectors/peers, probe coverage, IPv4/IPv6 coverage and gaps |

## Phase 0: Reconcile and establish trustworthy data

Outcome: subsequent UI work rests on observable, repeatable data rather than misleading green indicators.

Deliverables:
- Reconcile deployed bd87a80, its schema additions and the collector fix with reviewed repository history; retain original code/evidence.
- Complete DEV1 dependency/schema/config parity and reproducible native install/service instructions.
- Validate the collector fix through consecutive real collection cycles; enforce error propagation so failed writes cannot report success.
- Distinguish process liveness, dependency readiness, source freshness and measurement coverage.
- Define null/unknown/stale semantics for API and frontend; replace the retained-score behavior and hardcoded source-health labels.
- Create repeatable replay fixtures for normal routing, flapping, suspicious origins, missing sources, collector reconnects and recovery. Fixtures remain clearly labeled and outside public live results.
- Measure current traffic/ingest/storage and frontend performance; identify which README features actually work.

Acceptance:
- Consecutive collection windows advance source/database timestamps without silent batch loss; include retry and reconnect transitions.
- Stopping a test source produces a visible coverage warning and does not appear as Internet recovery.
- Empty results distinguish no observed incidents from no usable evidence.
- Schema migrations are repeatable and environments have a documented release identity.

Dependencies: none. Phase 1 design can start in parallel; live-data release depends on this gate.

## Phase 1: Design system and operator workflow

Outcome: a coherent product shell and interaction model.

Deliverables:
- Visual audit of the actual existing UI at desktop, NOC wall, tablet and mobile sizes.
- Information architecture: Overview, Investigate, Incidents, Diagnostics, My Network, Coverage; map mode is separate from workspace selection.
- Dark-first design tokens, readable type hierarchy, severity palette, non-color status indicators, component states and accessible focus.
- Responsive panel behavior with resizable desktop investigation panels and mobile detail sheets; remove reliance on fixed drawer offsets.
- Shared investigation state: selected resource/incident, filters, address family, observer set, visualization mode, live/historical time and comparison time.
- URL/deep-link and browser back/forward behavior; separate user display preferences from shareable investigation parameters.
- Wireframes and a reviewable interaction prototype for Overview, ASN, Prefix and Compare Routes.
- Plain-language summaries with technical details one action away; replace premature attacker/hijack wording with evidence-qualified labels.
- Explicit loading, empty, unavailable, stale, partial and permission states.

Acceptance:
- Operator can identify a significant issue within10seconds in a representative usability exercise and reach primary evidence within3intentional interactions.
- No mode switch loses selection/time/filters.
- Keyboard and reduced-motion flows work; mobile retains search and investigation essentials.
- Proposed default: 2D overview on first use; persist user choice, retain3D as a peer view. Default is a recommendation, not a user-selected requirement.

Dependencies: current-source/UI audit; can overlap Phase 0.

## Phase 2: Synchronized 2D map and 3D globe uplift

Outcome: the first substantial visible release.

Deliverables:
- 2D global map with pan/zoom, clustering, geographic aggregation, ASN/event markers, selected paths, legends and layer filters.
- Antimeridian/wrap behavior, overlapping entities and low-confidence/missing coordinates handled explicitly.
- Reuse the same filtered observation/incident model across2D/3D; do not independently invent map counts.
- Redesign3D camera/focus, selection hit targets, label density and restrained animation; preserve user camera position during live updates.
- Add an initial logical AS topology view for a selected investigation; do not imply that logical edges are physical circuits.
- Global overview with separate reachability, routing stability, performance, security and observation-quality indicators.
- Ranked incident rail, regional drill-down, universal search and persistent time/coverage controls.
- Graceful2D/list fallback when WebGL is unavailable. Choose2D rendering technology through measured tests; basic2D must not require the3D renderer.
- Suspend unused visualization rendering and hidden-tab activity while preserving state; reconnect intentionally without duplicate subscriptions or retry timers.

Acceptance:
- Same selected incident/resource and counts remain consistent across2D/3D/topology.
- No country/ASN is painted unhealthy solely because its registered location matches an incident.
- Filters, reset, selection, zoom, keyboard, touch and source-failure states pass browser QA.
- Initial overview content target<3 seconds on an agreed reference desktop/network using warm representative data; layer changes target<150ms excluding network work.
- Long-running display test demonstrates bounded memory and rendering activity.

Dependencies: Phase 1 state/design contracts and Phase 0 evidence semantics. Mockup review may precede live readiness; mocks must be labeled.

## Phase 3: Looking-glass replacement and entity investigation

Outcome: routine route investigation can be completed inside the product.

Deliverables:
- Search and canonical resource resolution for ASN, prefix, IPv4/IPv6 address, organization and hostname; show DNS-resolved addresses/time.
- Full ASN and prefix workspaces with routing/performance/security/incidents tabs and contextual evidence.
- Exact, covering and more-specific routes; origins, AS paths, next hops, standard/large/extended communities where available; explain community meanings with provenance.
- Compare multiple collectors/peers, geography and historical times; path and attribute diffs.
- Prefix visibility with an explicit eligible-observer denominator and stable comparison cohort.
- Route last-change time separate from route confirmation/feed freshness; distinguish missing observation from withdrawn route.
- Logical path versus measured traceroute clearly labeled. Router-specific policy/forwarding attributes appear only when a contributing integration actually provides them.
- Shareable investigations, text/JSON/CSV evidence exports and paginated server-side history, with redaction for private results.

Acceptance:
- Scenario: find a customer prefix, compare viewpoints, explain the path difference, inspect raw evidence and share the exact time window.
- IPv4 and IPv6, multi-origin prefixes, prepending, no-route and partial-observer cases work.
- Cached common entity requests target p95<1 second under the defined test load.
- No aggregate graph path is presented as a verified forwarding path.

Dependencies: Phase 2 shell; Phase 0 route normalization/history.

## Phase 4: Distributed diagnostics and performance monitoring

Outcome: the product can observe degradation that routing feeds do not reveal.

Deliverables:
- Source adapters for selected distributed measurements, starting with available measurements and focused on-demand tests.
- Evaluate RIPE Atlas/Globalping and source-specific capabilities, quotas, freshness, costs and redistribution constraints before committing to an integration.
- Choose probe cohorts by source ASN, region, network type and IPv4/IPv6 availability; show actual sources returned and uncovered requests.
- Ping, traceroute, repeated path/loss testing, TCP connection, DNS and suitable HTTP/TLS checks according to provider capability.
- Consented stable targets and statistically useful coverage; avoid assuming one target represents an entire ASN.
- Source/destination/protocol-specific baselines for latency percentiles, variation, loss and connection failures; time-of-day context and minimum sample rules.
- Distinguish suspect congestion, path detour, destination trouble and measurement-source trouble; correlate routing changes without claiming causation.
- Async test jobs with progress, cancellation, quota feedback, bounded retention and controlled public-target validation.
- Future throughput testing only against consenting endpoints with explicit traffic budgets.

Acceptance:
- Compare healthy and degraded paths from multiple independent sources with visible sample counts and baseline deviations.
- Intermediate-hop ICMP silence does not automatically become a forwarding-loss incident.
- A failed probe/provider produces an unavailable measurement state, not a destination outage.
- Repeated tests do not silently change the comparison cohort or consume unbounded credits.

Dependencies: Phase 3 resource/viewpoint model and secured diagnostics job path. Provider access/coverage may gate individual measurement types.

## Phase 5: Correlated incidents and historical replay

Outcome: transform observation volume into defensible, actionable incidents.

Deliverables:
- Normalized incident identity, lifecycle, deduplication, reopen behavior and explicit resolution evidence.
- Event taxonomy: instability/flapping, withdrawal/visibility loss, suspicious origin/subprefix announcement, suspected leak, performance degradation and reachability outage.
- Historical baselines, persistence/hysteresis and collector-session awareness.
- Evidence graph with data lineage; correlated downstream feeds do not count as independent corroboration.
- Explainable severity, impact and confidence; bounded affected ASN/prefix/region lists with observation coverage.
- Time-synchronized routing/performance playback, before/after path comparison and an incident narrative.
- Separate machine inference, provider statement and operator annotation; retain contrary evidence and alternate explanations.
- Public wording never promotes an RPKI-invalid route or origin change directly to confirmed malicious hijack.

Acceptance:
- Replay known or reviewed incidents and measure precision, missed incidents, detection delay and recovery delay by class.
- Tests cover legitimate multi-origin operation, route refresh, collector outage, sparse coverage, origin authorization changes and recurring incidents.
- Feed failures do not close incidents as recovered; closure distinguishes observed recovery from evidence expiry.
- No double-counted observations inflate severity or impact.

Dependencies: Phase 0 fixtures/lineage, Phase 3 routing history and Phase 4 measurement coverage for performance incidents.

## Phase 6: Public and ISP operating release

Outcome: a durable public service with richer private ISP workflows.

Deliverables:
- Public read-oriented browsing with clear coverage/methodology; accounts for saved workspaces, watchlists and resource-intensive diagnostics.
- My Network subscriptions for ASNs, prefixes, upstreams and selected destinations; alert deduplication, preferences and maintenance annotations.
- API/export contracts, scoped API keys, quotas, request budgets and private evidence access control.
- Optional contributing-ISP probes, controlled router views and BMP-based observations, with deliberate public/private publication rules.
- NOC wall mode, shared read-only incident links and usable mobile triage.
- Capacity-based retention, compression, backups, restore verification and runbooks; dependency/source monitoring and incident response.
- Separate failure domains for serving, ingestion and measurement execution as measured demand requires; do not assume one LXC is a global-scale service.
- Browser, accessibility, incident-storm and 24-hour display soak validation with a documented reference dataset and hardware.
- Production release candidate, UAT scenario review, bounded rollout and verified rollback.

Acceptance:
- Public and private workspaces cannot expose each other's private measurements or configuration.
- Probe jobs enforce quotas and cannot access private/internal targets through public diagnostics.
- Failure of one external source degrades coverage without taking down the overview.
- Benchmark at measured representative load plus a defined burst multiplier; publish tested limits instead of claiming unlimited global coverage.
- Operator accepts the UAT journeys; production promotion is explicitly authorized separately.

Dependencies: prior capabilities plus demonstrated source/serving capacity.

## Release milestones and dependency order

| Milestone | Includes | Visible result |
| --- | --- | --- |
| R1: Visual preview | Phase 1 + Phase 2 design; Phase 0in parallel | Redesigned shell,2D/3D switch and sample investigation; any non-live fixtures labeled |
| R2: Live visual beta | Phase 0 + Phase 2 gates | Honest global overview with live evidence and coverage |
| R3: Looking-glass beta | Phase 3 | Useful ASN/prefix and viewpoint route investigations |
| R4: Performance beta | Phase 4 | Distributed diagnostics and evidence-backed degradation |
| R5: Incident beta | Phase 5 | Correlated incidents and historical replay |
| R6: Public/ISP release | Phase 6 | Operable public product with private ISP workspaces |

Use small reviewed vertical slices through DEV1 -> UAT -> separately approved PROD. Design and reliability can overlap; do not release live visual claims before Phase 0. No calendar estimates are asserted until the audit establishes working capabilities, dependency effort, source access and target scale.

## Implementation jump starts

- Extend frontend/src/store/nocStore.ts into a versioned investigation-state contract. Separate workspace from visualization mode and centralize address family, observers, selected resource and time.
- Refactor frontend/src/App.tsx into a responsive shell and renderer slots. Correct stale-score handling and verify WebSocket teardown/reconnect behavior.
- Keep GlobeView useful while extracting common entity selection, filters, legends and layer data for a new 2Dview.
- Evolve ASSidebar into shared entity investigation panels; evolve PathPanel into viewpoint-aware route comparison with evidence.
- Replace unsafe casts/loosely typed data in critical contracts with normalized typed responses, including unknown/partial/source-error states.
- Keep detector, provider adapters and display logic separate. Schema migrations and replay datasets version alongside the implementing change.
- Update README/SYSTEM_PROMPT/deployment docs to actual stack and environments after reconciliation.

## Definition of done for every slice

- Working behavior is checked against real data or clearly identified deterministic fixtures.
- Appropriate build/static checks plus meaningful behavior tests pass.
- API/source failure, stale data, empty data, reconnect and recovery states are verified.
- Desktop/mobile/accessibility and 2D/3D state preservation are checked when relevant.
- Performance is measured for rendering and serving under an explicit workload.
- Evidence provenance, limitations, migrations, rollback and release identity are recorded.
- UAT review evidence and outstanding limitations remain explicit. Tests passing does not imply human appearance acceptance or production deployment authorization.

## Twelve-month history and analysis

The current budget-constrained proposal is [RETENTION-150G-350G-PLAN.md](RETENTION-150G-350G-PLAN.md), version 2.3. David set 150 GiB of container storage plus 350 GiB of attached NAS. This supersedes the earlier full-detail annual retention policy and multi-terabyte estimate. Current containers remain 200 GiB PROD/100 GiB UAT and no NAS mount was observed; this document neither shrinks filesystems nor attaches storage.

Target: twelve months of trends, incident history, selected evidence and periodic checkpoint comparisons. Routine raw/normalized detail is approximately 24 hours local and initially 72 hours on NAS, targeting seven days only after compression/burst profiling. Global/regional hourly trends and daily ASN summaries span twelve months; hourly ASN history spans 90 days, with a bounded watchlist cohort retained hourly for the year. Older arbitrary route replay and full new-detector reanalysis are unavailable after routine raw expiry unless their evidence was selected and retained.

Allocation: local 20 GiB OS/application + 20 GiB current routes + 35 GiB recent detail + 15 GiB metadata/cache + 15 GiB database working space + 45 GiB reserve = 150 GiB. NAS 120 GiB rolling detail + 50 GiB annual analysis + 30 GiB checkpoints + 35 GiB incident evidence + 5 GiB catalogue + 20 GiB staging + 90 GiB reserve = 350 GiB. Backups are not a second funded archive copy: verify existing backup coverage/capacity and restore before destructive expiry.

Execute fixed-budget phases 0–2 first: profile/preserve and qualify NAS/backup placement, build archive/rollups/selected evidence, then safe local expiry and receiver recovery. Historical UX and annual-volume/restore qualification follow. Do not silently shorten published windows or erase annual evidence to meet a quota; pause expansion/intake before exhausting recovery reserves. Full bulk-data archival/replay is development-only, per David's clarification. The first non-destructive pilot verified all 1,828,156 journal records on NAS, added sampled format profiles, started the finite 72-hour study and fixed capacity-stop reporting. A temporary UAT safeguard pauses future beat scheduling below 30 GiB free. CT protection prevented a direct bind mount; it was left enabled and host-side NAS publication is used. No expiry, receiver guard increase, resize or production deployment occurred. Evidence: [RETENTION-PILOT-20261023.md](RETENTION-PILOT-20261023.md).

## Immediate next work

1. Complete the fixed-budget growth/format/checkpoint study and annual cardinality forecast. Atomic provider history and observed-cohort hourly/daily summaries are now in a bounded UAT pilot; qualify ongoing backup coverage and quota. See [RETAINED-EVIDENCE-20261023.md](RETAINED-EVIDENCE-20261023.md).
2. Implement verified archival/protection and safe local expiry, resume continuous RIS receivers within the local/NAS budgets, and repair the recorded interruption before expanding ingestion.
3. Add periodic independent RIB reconciliation, database-failure/restart soak and serving concurrency limits.
4. Expand active-measurement endpoint/network diversity and build evidence-linked incident correlation with persistence, contrary evidence and source-lineage rules.
5. Complete rendered accessibility/mobile/NOC-display review, then qualify the exact release scope for production. Existing production authorization does not remove technical release gates.

## Reference specifications and provider documentation

These references informed capability boundaries; they are not claims of integration completion.
- RIPEstat looking glass: https://stat.ripe.net/docs/data-api/api-endpoints/looking-glass.html
- Looking-glass command interface, RFC8522: https://www.rfc-editor.org/rfc/rfc8522.html
- Route-origin validation, RFC6811: https://www.rfc-editor.org/rfc/rfc6811.html
- BMP, RFC7854: https://datatracker.ietf.org/doc/html/rfc7854
- RIPE Atlas measurement definitions: https://atlas.ripe.net/docs/apis/rest-api-manual/measurements/creating-measurements/definitions/
- Globalping supported measurement types: https://github.com/jsdelivr/globalping/blob/master/public/v1/spec.yaml

## Provider history qualification — 2026-10-23

The additive UAT capture retains Atlas/mesh/IODA revisions and collection cycles in the same transaction as latest evidence. Mesh samples are deduplicated across overlapping fetch windows; corrections are new versions. The first 728 cohort summaries are verified on NAS. Capture is finite and bounded, with explicit gaps at limits; summaries state unknown expected coverage. No global score, complete historical feed, annual capacity qualification, historical UI/API, journal expiry or receiver restart is implied. Full archive tooling remains development-only. Production application and native API/worker/frontend are unchanged.

## Operational direction — 2026-10-24

David held backup coverage and prioritized a self-imposed NAS quota, collection/validator repairs, journal rotation and resuming continuous RIS. Backup coverage is not a gate for this requested operating slice. Continuous RIS resumed in development at 00:27:37 UTC, managed quota/publication is active, and real Atlas/BGP/RPKI checks passed. The protected local detail window is 24 hours; private development NAS raw retention is 72 hours. Production promotion includes a separately running maintained-route receiver, with full archives remaining private to development. Existing gaps remain visible until actual replay completes. See [OPERATIONS-LIVE-20261024.md](OPERATIONS-LIVE-20261024.md) for controls, measured evidence and deployment state.
