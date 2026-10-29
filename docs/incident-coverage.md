# Incident cases, responsiveness and actionable coverage

The incident workspace anchors an investigation to one exact retained continuous observation. Its surrounding thirty-minute routing timeline can be compared with current RPKI validity, nearby sampled reports and configured endpoint measurements that actually match its resource and time. A case is an investigation, not a confirmed incident, a global-impact estimate or a claim of recovery.

## Investigation workflow

Open a continuous candidate from Issues, an observation from retained ASN/prefix history, or a change from ASN-to-ASN paths. The case preserves the anchor, its before/after paths, recorded ASNs entering or leaving that path, individual source clocks, overlapping source gaps and links to full original retained records. Baselines and continuity resets remain separate from actual changes.

The timeline uses committed observer checkpoints and an absolute five-minute expiry. Its case identity and page cursor are carried in the shared URL. Next and First captured page keep the same capture; Refresh starts a new one. Late commits cannot enter an older capture. Changed filters, foreign cursors, expired captures and reduced retention require an explicit refresh. The anchor remains shareable while its original evidence is retained.

Impact counts cover the returned page only. Aggregate withdrawal prefixes are correlated to their own recorded paths before ASN/resource matching. Display caps are labeled as lower bounds, and the full original observation remains available. A prefix set and an unrelated ASN elsewhere in that aggregate do not establish affected-prefix involvement.

Current RPKI checks retain their generated and checked times. Historical authorization remains unknown. Endpoint measurements match the actual target IP/prefix, or an endpoint ASN for an ASN-scoped case, and the selected time range. Different source lineage and clocks remain visible. An empty measurement result does not become healthy routing or prove that no other public measurement exists. Public probe-selected ping/traceroute investigation is linked with the same time context; this release schedules no new probes.

## Coverage workflow

Coverage and gaps shows one captured routing matrix by viewpoint and address family, with current source continuity and a next investigation action. CIDRs mean exact prefixes. IP searches select each view's longest present prefix before ASN matching, preserving disagreements. ASN-only cells use one existence example per view/family and do not pretend to count every route.

The performance section lists only configured measurements matching the actual target IP or recorded endpoint ASN. Other public probes and measurements can be discovered separately. Measurement-source failure leaves routing coverage available, with performance explicitly unavailable. Regions locate configured observation endpoints; they do not locate all routed networks, affected users or an entire ASN. No global health percentage or coverage denominator is fabricated.

## Responsiveness and UI corrections

IP lookups probe the possible covering networks through existing keys in the captured catalogue or current maintained RIB. Pinned current/history versions and origin disagreements are preserved. This changes queries, not indexes or database tuning. Results and timings are compared under a consistent read snapshot, including cases where the replacement costs more.

New long path-history captures scan recent ranges first, then progressively older ranges through an explicit continuation cursor. A bounded empty page can have older evidence. The requested period, scanned range, original capture, retention floor and absolute expiry remain separate. Existing version-one cursors retain their original contract during rollout.

The two public-measurement forms have distinct React keys, including when IDs are blank, so clock updates do not duplicate forms. Section jump buttons retain URL investigation context. Cases use ten-row captured pages, responsive panel spacing, labeled controls, keyboard form submission and individual source times. Existing 2D/3D/topology and ASN/prefix navigation remain available.

## Qualification and operations

- `tests/test_incident_evidence.py` and existing path semantics tests cover qualified correlation, baseline/change separation, scoped aggregate impact, display bounds and unknowns.
- `scripts/test-incident-investigations-db.py` uses a strictly isolated development database. It tests both catalogue engines, thirteen-day evidence, full pinned pagination, late commits, foreign/scope/expiry/retention refusals, separate RPKI clocks, matching measurements and measurement-source failure isolation, plus provider GET/refusal contracts.
- `scripts/qualify-incident-coverage.py` runs bounded live acceptance against retained production observations and existing public measurements, without scheduling tests or spending credits.
- TypeScript/Vite and focused lint validate changed frontend files. Rendered project-scoped Chromium checks cover desktop/mobile workflows, timer duplication, captured paging and keyboard form submission. This does not claim a full screen-reader, physical-device, WCAG or representative public-load certification.

Publication changes only owned API modules and static assets. PostgreSQL, collectors, diagnostics, catalogue serving, retention policy and existing durability settings are preserved. Recovery uses owned file hashes and original images; obsolete diagnostic schema rollback is not part of this release. Private operational evidence contains the measured before/after counters, service-user startup/recovery tests, browser artifacts and limitations. No production telemetry or private repository ancestry is published to GitHub.

Authored with OpenAI Codex.
