# R1 UI and capability audit — 2026-10-22
Prepared by OpenAI Codex. Scope: existing DEV1 UI, source-level capability checks, four interactive design screens, and bounded collector validation.

## Deliverable

Preview: http://192.168.1.65/design.html

The preview is a separate Vite entry. It does not query live APIs or run diagnostics. All networks, routes, incidents, statuses and measurement values are labeled fixtures using documentation ASNs/prefixes. The existing application entry and running services were not replaced. DEV1 publication adds design.html and content-hashed assets only; scripts/publish-design-preview.py refuses other hosts and protects the existing index.html hash.

Implemented screens: Global Overview, ASN Investigation, Prefix Investigation, Compare Routes.
Views: 2D Natural Earth map, rotatable orthographic globe, illustrative logical AS topology. The preview globe uses a D3 projection; this is not the existing production WebGL engine or a completed production renderer integration.

## Observed gaps and disposition

| Finding | Evidence | Priority / disposition |
| --- | --- | --- |
| Existing UI does not distinguish insufficient evidence from normal Internet conditions | Browser showed OFFLINE/loading while footer said nominal; API status returned score100 with no updates | P0. Preview explicitly withholds global health and has no-coverage/partial scenarios. Live API correction remains open. |
| DEV1 frontend cannot reach its API through nginx | /api/v1/status via nginx returns404; direct :8000 works; enabled default nginx configuration has no proxy | P0. Native parity fix remains open; preview deliberately uses no live API. |
| DEV1 collector environment is incomplete | pip inventory lacks tenacity, netaddr, beautifulsoup4, lxml, praw, websockets, websocket-client and socketio packages present on PROD; installed core client versions differ from repository pins | P0. Inventory confirmed. A test-only tenacity target already present under /tmp was used for validation; live venv unchanged. |
| Stored score can outlive current evidence | App.tsx only updates healthScore for non-null responses | P0. Define live nullable/freshness contract before wiring preview to APIs. |
| Navigation is abbreviated and context is fragmented | Existing GLOB/EVEN/PATH/BASE/COMM controls; store mixes active workspace and renderer; repeated PATH control | P1. Four named workspaces and shared URL state implemented in preview. |
| Operator investigation depth is missing from the visible journey | No universal resource search or side-by-side viewpoint workflow in audited shell | P1. Fixture search, ASN/prefix workspaces, route differences and raw evidence implemented for review. |
| Fixed desktop drawer/rail layout and separate mobile shell need redesign | Source uses fixed right:300 drawer offset; browser mobile drops investigation breadth | P1. Responsive panels and mobile navigation implemented in preview; all four workspaces remain available. |
| Hidden globe/reconnect lifecycle need validation | Legacy globe remains mounted under visibility:hidden; reconnect timeout lacks explicit cancellation in cleanup | P1. Preview conditionally mounts workspace content and uses no live subscriptions or animation loop. Legacy fixes remain open. |
| Raw AS-path evidence is modified during ingestion | parse_ris_message globally removes duplicate ASNs with a seen set | P1 for looking-glass fidelity. Preserve original prepending/loops and derive simplified display paths separately. Not changed here. |
| Corroboration may share an underlying source | RIS observations are enriched using RIPEstat bgp-state from RIS | P1 for confidence. Model source lineage before independent-confirmation claims. Not changed here. |
| Existing frontend bundle is large | Build main chunk approximately1MB before gzip; existing source and dependencies remain | Performance backlog. New preview entry is approximately102KB plus shared countries/runtime chunk431KB before gzip. Not a completed capacity benchmark. |

The inventory above is bounded evidence, not a full security, deployment or external-provider capability audit. No installed version or README feature is treated as proof of working behavior.

## Shared investigation contract

frontend/src/preview/investigation.ts defines validated workspace, map mode, selected ASN/prefix, time window, address family, severity, observer cohort, scenario and comparison observer. Hash URLs preserve all fields; browser back/forward and reload restore context. All accepted values are bounded to the available fixture capabilities.

Display preferences/camera are retained in memory while navigating the preview. Persistent user camera preferences and live historical snapshot identifiers remain future work.

## Browser and behavior verification

- Existing desktop and390px mobile UI inspected.
- All four preview screens visually inspected; screenshots retained under docs/evidence/ui-r1.
- 2D ->3D ->topology switch preserves selected resource/time/filter context.
- Time filter expands2fixture incidents to3;IPv6 filter narrows to1.
- Topology node selects AS64498; ASN navigation retains selected IPv6 prefix/window.
- Prefix ->viewpoint comparison works; Singapore selection changes path and explanatory text.
- Reload retains comparison observer; browser Back restores Amsterdam.
- Search AS64496 opens its ASN workspace; documentation IP/prefix search is implemented.
- No-coverage scenario shows unknown/no evidence and withholds scores; delayed-observer scenario reports partial coverage.
- Evidence dialog opens with confidence/limitations, traps backward Tab, closes with Escape and restores focus to the Inspect button.
- Mobile layout checked at390x844 CSS viewport; document scroll width375, with internal table scrolling instead of page-wide overflow.
- Browser console reported no errors in the preview during these checks.
- Normal screenshot capture succeeded. Some full-page screenshot/scroll commands timed out; screenshots record successful captures only. Mobile screenshot output is downsampled by the capture tool and is supporting layout evidence, not pixel-accurate accessibility proof.
- Human design acceptance, full browser matrix, screen-reader review, touch/drag edge cases, contrast measurement, long-running display and performance benchmarks remain pending.

## Automated verification

npm run test:preview:6tests pass, covering URL context round trip, invalid URL values, incident filters, unknown/partial score handling and consistent comparison fixtures.
npm run build: TypeScript and Vite pass for both application and design entries.
Targeted eslint on new preview TypeScript/React files passes.
git diff --check passes.

Explicit empty PostCSS configuration prevents Vite walking into an invalid parent-level configuration on the authoring host. Tailwind continues through the existing Vite plugin. Existing root application main.tsx is unchanged.

## Bounded real-source collector validation

Candidate: openai/collector-loop-lifetime at da6a6f8, isolated /tmp/inet-review-20261022 source on DEV1.
Execution: same persistent-loop bridge across two consecutive real RIS cycles, rrc00 only,8-second streaming windows; each cycle bounded to24seconds.
Cycle1: rows0 ->7721; source timestamp2026-10-22T15:58:16.150Z.
Cycle2: rows7721 ->14822; source timestamp2026-10-22T15:58:35.170Z.
Both cycles wrote new observations and closed the pool cleanly. No event-loop/batch-write errors were reported. Real source observations were written to DEV1 only; this was not a read-only test.
This does not prove scheduled Celery-worker recovery, full-five-collector capacity, sustained operation or correctness of anomaly classifications. Existing live workers and PROD remain unchanged.

## DEV1 release receipt

Archive SHA256: a3a9bedc8701bafd12dc44eb203307629f4a909a657d7aea14d5ead962282451.
Verified at authoring host, Proxmox host and publisher.
Published manifest: /srv/inet-health-design-r1/published.json.
Publisher confirmed existing index.html unchanged.
Entry: /var/www/html/design.html. No nginx reload or service restart was required.

## Screenshots

- [Global Overview](evidence/ui-r1/overview.png)
- [ASN Investigation](evidence/ui-r1/asn.png)
- [Prefix Investigation](evidence/ui-r1/prefix.png)
- [Compare Routes](evidence/ui-r1/compare.png)
- [Mobile overview](evidence/ui-r1/mobile.png)

## Next bounded work

1. Review the four design screens with David and carry appearance feedback into the shared shell.
2. Complete native DEV1 dependencies/API proxy and live freshness/readiness contracts, with explicit worker-drain gates for any service replacement.
3. Wire one real ASN/prefix investigation end to end before expanding the UI with additional features.
4. Preserve raw AS paths and add source lineage before interpreting confidence as independent corroboration.
5. Continue reviewed DEV1 ->UAT progression; production release remains a separate authorization.
