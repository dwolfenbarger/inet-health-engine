# Production usability — immediate release priority

David's direction, October 23, 2026 (America/Chicago): make the existing collected data useful in production before expanding collection. Prepared by OpenAI Codex.

This is the next release obligation under the global Internet health/NOC charter. It supersedes the sequencing of the broader roadmap. Backup coverage remains held. Additional telemetry sources and broader retention work are deferred, except repairs necessary to keep current collection, quota enforcement and journal rotation operating. Do not turn infrastructure work into another prerequisite for delivering the investigation experience.

## Problem confirmed in production

At approximately 21:34 Central, the default fifteen-minute view contained 1,213 distinct origin ASNs in a capped 20,000-update sample, but returned only twelve overview ASNs and 200 route rows. The maintained view also caps results at 200 without pagination and derives overview totals from that small result set. Both ASN searches match origins only. Clicking a transit ASN can therefore lead to an empty origin-only investigation. The 2D/3D map plots collector cities. ASN identification, metadata and evidence integration are incomplete.

Production has substantially more maintained routing evidence than these screens expose. Runtime baseline is c8c108f; review baseline is 114c45d. Resumed collection did not complete the application charter. No production usability fix is claimed by this priority document.

## Required implementation order

1. **Observed ASN inventory and truthful overview.** Build a searchable, paginated inventory from all eligible maintained observer tables, independently of recent update activity. Preserve origin, transit and peer roles. Overview totals must describe the full indexed observation scope, with snapshot/as-of time and observer continuity, rather than the first page. Make maintained evidence the normal starting point; recent updates become a clearly named activity view. Provide actual result totals or an explicit unavailable count, never page length presented as the total.
2. **Complete route and path investigation.** Add stable cursor pagination and filters for prefix, address family, viewpoint and ASN role. Preserve resource and filters when navigating from a path hop to an ASN. Separate unique prefixes from per-observer route entries. Expose all eligible collected rows through browsing rather than simply increasing the response cap. Handle AS_SET/ambiguous membership explicitly and preserve raw attributes.
3. **Useful ASN detail.** Present network identity/name and provenance, observed prefixes, origins/transit appearances, peers and observed adjacencies, path evidence, RPKI results, recent changes, existing anomaly candidates, measurement evidence and source coverage in one investigation. Use cached authoritative metadata where available and display missing metadata honestly. Observed adjacency is not a confirmed commercial relationship. Unsupported measurements must show what is missing, rather than an empty panel or a healthy result.
4. **Visual navigation that supports the charter.** Connect directory, ASN detail, prefix, viewpoint comparison, topology and 2D/3D views with shared context. Distinguish collector geography, registration geography and measured endpoint locations. Do not invent geographic placement for ASNs or imply that the current collector map is an Internet health map. Make the ASN/path evidence reachable directly from the visual views.
5. **Production acceptance.** Validate the same workflows on real UAT data, promote the tested release under existing production authorization, then repeat them in production. Record screenshots, endpoint/database reconciliations, timing and exact deployed commit. Report incomplete acceptance items explicitly; collection/service liveness alone cannot pass this release.

## Acceptance workflows — all required

| Operator task | Required result |
|---|---|
| Find a stable ASN with no updates in the recent window | It remains discoverable through maintained evidence; its recent activity may correctly be zero. |
| Browse the ASN directory beyond the twelve overview entries | Subsequent pages work, search covers the inventory, and displayed totals reconcile with the inventory snapshot. |
| Investigate an ASN with more than 200 observed routes | Browse beyond row 200 without silent truncation; no duplicates or omissions within the declared pagination snapshot. |
| Click a transit ASN in an observed path | Open that ASN with its transit evidence and preserved role/context, even when origin results are empty. |
| Inspect IPv4, IPv6 and a four-byte ASN | Filters, counts, paths and raw evidence retain correct identities. |
| Compare one prefix from different viewpoints | Show differing paths, source times, continuity and origin validity without merging distinct evidence. |
| Investigate performance or a routing anomaly | Reach the underlying observed evidence; distinguish an unmeasured ASN, a candidate and a confirmed condition. |
| Search an absent ASN or encounter missing/stale metadata | Explain whether the ASN is unobserved, metadata is unavailable, a filter excludes it, or collection is stale. |
| Refresh or share an investigation | Restore ASN/prefix, role, viewpoint, family and selected visual context. |
| Use the production UI on desktop and a narrower viewport | Primary flows are visible, keyboard reachable and usable; no clipping or dead-end controls. |

Use at least ten actual observed ASNs spanning these cases, selected from the current data rather than fabricated fixtures. For a fixed inventory generation, reconcile directory totals and all pages against the same underlying scope. Record chosen resources and results in the release evidence. Exercise metadata failure, stale inventory and unrepaired observer gaps. A complete directory of observed ASNs is not a claim to know every allocated or globally reachable ASN.

## Engineering and validation constraints

- Keep response bounds while adding pagination and aggregate serving. Profile existing indexes and query plans before selecting the implementation. Do not run full-table aggregates for every interactive request.
- Publish inventory/aggregate generations atomically, with age, source scope and continuity. Support restart/resume and concurrent route changes without serving a partially built index as complete.
- Benchmark default directory, filtered ASN, route pagination and transit lookup under concurrent live collection. Target p95 below two seconds for ordinary cached/indexed investigations; report measured exceptions and resolve them before claiming acceptance.
- Keep existing intake and retention safeguards. Use additive schema/index changes and qualification in UAT; preserve rollback. Drain native workers before any deployment that requires replacing their code.
- Verify rendered behavior using the available LAN review environment, including CT118 if necessary and accessible. The earlier cloud-browser certificate failure does not establish that the UI works and must not be bypassed. Resolve the review access route or record visual acceptance as blocked, never substitute API success for rendered proof.
- Do not mark this work complete because a source connects, an endpoint returns HTTP200, more rows are collected, or another plan is written. Completion means the production acceptance workflows pass.

## Status

Priority and acceptance criteria recorded. Implementation and production acceptance remain open. This document is the controlling checklist for the next application release; the larger roadmap remains valid after this obligation.
