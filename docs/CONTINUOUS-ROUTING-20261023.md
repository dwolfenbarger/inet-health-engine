# Selected observer routing canary — 2026-10-23

This phase replaces intermittent sampling with a continuously maintained view for selected RIS peers, and adds a separately sourced RouteViews archive view. It is a UAT canary, not full global coverage or a production cutover. Production remains on master `19c2923135ce40620bd8bc7b301b68e4c828298d` (PR #6).

## Delivered

- Additive routing tables hold collector + peer IP + peer ASN identity, session epochs, raw RIS messages, transactional checkpoints, current routes and withdrawal tombstones, gaps, and archive URL/hash/audit records. Existing observation history is retained.
- RIS Live receiver uses a bounded queue and commits messages, route changes and the checkpoint atomically. Retries retain the batch. Duplicate message IDs do not advance the sequence. It has no 50,000-update window cap.
- MRT recovery loads an audited full snapshot for the selected peer, then requires every archive interval bridging it to the live feed. Snapshot time is separate from a route's old last-change timestamp. Replay ordering prevents older archives from resurrecting a live withdrawal. Session changes invalidate the previous epoch; selected ADD_PATH is refused.
- RouteViews uses a full selected-peer snapshot and contiguous update archives. Its evidence time and publication delay remain explicit. The recovery loop yields after four new files per observer and resumes from committed file identities.
- `/api/v1/noc/routing/{observers,routes,gaps}` exposes continuity separately from health. Route queries support exact prefix, IP longest-prefix lookup, observed origin ASN, family, collector and observer. Queries return at most 200 routes. An IP lookup falls back to a present covering route after a more-specific withdrawal.
- The NOC interface adds **Maintained RIB** alongside sampled updates. It preserves mode/resource/viewpoints in the URL, shows each observer's baseline and evidence times, IPv4/IPv6 snapshot counts and pending gaps, and keeps global health unknown. Raw path segments, repeated ASNs and archive attributes are retained; complex paths are not flattened into invented AS sequences.

## UAT evidence

Native UAT LXC 554 (`192.168.1.75`) runs the interface/API candidate `0766513c923cb2bbe7110188779b476251536e73`. Its rollback is `/srv/inet-health-backups/20261023T060335Z`; the previous PR #6 checkpoint is `/srv/inet-health-backups/20261023T055253Z`.

| View | Peer | Baseline |
|---|---|---|
| RIPE RIS rrc23, Singapore | AS133210, `2001:de8:4::13:3210:1` | 261,911 IPv6 prefixes, 2026-10-22 08:00 UTC |
| RouteViews KIXP, Nairobi | AS30844, `196.223.21.44` | 1,073,233 IPv4 prefixes, 2026-10-23 04:00 UTC |

The initial RIS gap was repaired with 258 contiguous update archives (455,718 decoded prefix changes). Live commits continued during bootstrap. These counts describe the baseline and replay, not unique affected networks or global health. Current tables can differ from baseline counts and retain withdrawal tombstones.

Validation: 36 Python tests, six existing preview-context tests, frontend build and targeted lint passed. Real PostgreSQL regressions exercise atomic failure, deduplication, full four-byte ASNs, peer separation, session invalidation, stale archive refusal, gap boundaries, IP fallback and HTTP input limits; all test records roll back. Live HTTP checks resolve `1.1.1.1` to `1.1.1.0/24` through the RouteViews peer and `2606:4700:4700::1111` to `2606:4700:4700::/48` through the RIS peer.

A production-size read exposed a 14.1-second default query. A concurrent recent-route index reduced observed UAT responses to 0.07–0.3 seconds. This is short operational evidence, not a concurrency benchmark or an SLO. Existing deployments can create `routing_routes_recent` concurrently before serving the new view; fresh initialization includes it.

The initial Python/Rust parser adapter failed because a PyO3 parser moved between threads. Fixed construction, iteration and destruction to one dedicated thread before any snapshot was marked complete. The successful full imports and replay used that fix.

## Operations and remaining gates

- Dedicated `inet-routing-stream` and `inet-routing-recovery` services read isolated code under `/opt/inet-routing-canary/collectors`, observer configuration `/etc/inet-health/routing-observers.json`, and disposable downloads `/var/lib/inet-health/routing-cache`. They are running canaries, not enabled at boot. Configuration is checked into `observers-uat.json`; the installer refuses production and refuses configuration replacement.
- Services have singleton locks, memory/CPU bounds, graceful signal handling and no forced stop timeout. Receiver capacity pauses at a 2-GiB journal or under 20 GiB root free space; archive downloads are bounded. No raw-history retention deletion is implemented. Capacity recovery remains an operator action.
- Native app promotion stops beat, proves active/reserved/scheduled work is zero, verifies artifacts and retains rollback before worker restart. No queue purge or forced termination was used.
- A controlled receiver stop retained sequence 7,358, drained its queued messages, exposed an open gap, then resumed with the first new source event at 05:58:23.050 UTC. The gap starts at committed source time 05:57:25 UTC. The interface reported `gap` while waiting for publication. The 05:55 archive was subsequently applied (2,285 decoded prefix changes), repairing the interruption by 06:06 UTC. Both gaps are now repaired; the receiver retained its generation and advanced beyond sequence 9,227.
- UAT's local validator evidence became stale during the canary; its existing collector refused stale validation and the UI shows collection error/unknown proof. Production independently reported successful validation at 05:53:41 UTC with 1,027,030 validated VRPs. Investigate UAT refresh progress before relying on its RPKI proof.
- Rendered browser/accessibility QA remains pending: the available fallback previously encountered a certificate error on the private-IP surface, and no certificate warning was bypassed.
- Geographic and AFI coverage is deliberately narrow: one Asian IPv6 observer and one African IPv4 observer. Add independently chosen IPv4 and IPv6 peers in every target region only after capacity, quality and recovery qualification. Periodic independent RIB reconciliation, restart/DB failure soak, retention, concurrent-query limits, transit-AS investigation and complete route history remain open.
- Atlas evidence still covers K-root built-ins, not global ASN performance. Radar credentials, broader active measurements, M-Lab/HERMES baselines and lineage-aware incident correlation remain separate roadmap work. BGP observations do not establish congestion or application reachability.

Primary formats and provenance: [RIPE RIS MRT](https://ris.ripe.net/docs/), [RIS Live](https://ris-live.ripe.net/manual/), [RouteViews archives](https://archive.routeviews.org/), [BGPKIT Python](https://github.com/bgpkit/pybgpkit).
