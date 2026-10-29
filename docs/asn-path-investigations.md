# ASN-to-ASN path investigations

The dedicated **ASN-to-ASN paths** workspace combines advertised routing, retained changes and recorded public probe measurements for a technical ISP/NOC investigation. Choose a source ASN and destination origin ASN, then optionally focus an exact prefix or an IP address. Shared URL context includes the ASN pair, resource, family, collector, history window, probe and measurement.

## Advertised paths

- Every displayed segment is a suffix of one observed AS path. Separate observations are never stitched into a path.
- The destination is the route origin; the source must appear in that path. This does not establish the source router's selected forwarding route.
- Consecutive prepends, AS sets, confederations, unknown segments and loops retain their evidence and ambiguity. Transit ASNs open the existing ASN investigation.
- CIDR searches are exact-prefix searches. IP searches select the longest present prefix in each viewpoint **before** evaluating the requested ASNs. A disagreeing origin or absent source stays visible instead of falling back to a less-specific matching route.
- Route pages pin the catalogue generation. Current continuity is shown separately from the captured browsing snapshot. Expired generations require a refresh.

## Retained changes and observed impact

The history API accepts one hour, one day, seven days or fourteen days and an optional UTC end time. Source/destination matching requires both ASNs in the same recorded path, including a victim's before-path and the correct prefix of an aggregate withdrawal. It does not correlate unrelated prefix paths merely because they share an event.

Pages fence committed observer heads and retain an absolute five-minute expiration. A bounded candidate scan can produce an empty page with a next cursor; the UI keeps that next page available. Later commits and live updates cannot leak into an existing investigation. Retention floors, source gaps and the effective window stay explicit. Counts describe this page's observations, recorded prefixes and viewpoints; they are not a global impact percentage.

Exact retained event details are available from each historical entry. Separate MRT RIB repair does not reconstruct missing diagnostic observations. The configured fourteen-day target accumulates forward and remains subject to the existing allocation cap and reserve; it does not create earlier evidence.

## Measured paths

Probe discovery selects existing connected public RIPE Atlas probes in the source ASN/family. Measurement discovery selects existing ongoing public ping/traceroute measurements for that probe and targets attributed to the destination ASN. Known historical measurement IDs can also be inspected. This release only reads public provider APIs; it does not schedule tests or spend measurement credits.

Results show the latest two distinct observations within one hour ending at the requested time. Measurement/probe/type/family/target identity and result timestamps are validated. Individual TTLs, responding interfaces, ECMP responses, missing TTLs, timeouts, errors and measured RTTs remain visible. Ping packet counts and loss use real endpoint results. A hop timeout does not establish forwarding loss; adjacent-hop RTT differences do not establish congestion. A responding-address difference is not asserted to be a forwarding-route change.

Hop ASN hints use current longest-prefix routing origins from selected maintained views. They do not identify router ownership or historical ASN attribution. Private/reserved addresses, missing observations and bounded mapping failures remain unknown. BGP and measurement timestamps and viewpoints remain separate; overlap establishes correlation only. Public provider history is fetched on demand, without extending local raw-measurement retention.

## Resource bounds

Public-provider requests use a fixed HTTPS API, GET only, two admitted requests, bounded input/timeout/result counts and short Redis caching. Hop mapping selects at most 32 unique addresses, four concurrently, with a two-second query budget each. Discovery uses ten entries per page and an explicit page cap. Route/history responses use bounded pages, existing catalogue/diagnostic indexes and existing pools. No new collector, subscriber, scheduled measurement, schema migration or index is introduced.

## Verification

- `python tests/test_path_evidence.py`: route semantics and measured-result parsing.
- `scripts/test-path-investigations-db.py`: isolated PostgreSQL/ASGI qualification of both catalogue engines, pinned pages, LPM/origin disagreement, historical victims/withdrawals, committed-head fences, empty pages, expiration and mocked provider contracts. Use only an explicitly isolated QA database with its enforced prefix.
- `scripts/qualify-path-investigations.py`: bounded live candidate/public API acceptance, including real existing public traceroute/ping results and unchanged catalogue behavior. It performs no measurement creation.
- Frontend TypeScript/Vite build and ESLint on the two changed TSX files. Whole-project legacy lint findings remain a separate backlog.
- Actual service-user cold startup and owned API source recovery were rehearsed against the isolated QA database. Production publication preserves PostgreSQL, source, recovery, diagnostics and catalogue service state.

Rendered desktop/mobile/accessibility acceptance and representative public concurrency remain open. Selected routing peers and available public probe measurements do not establish complete global coverage. The next charter phase is scoped incident/impact correlation across qualified evidence, followed by watchlists/alerts and sustained public-load and analytical-retention acceptance.

Authored with OpenAI Codex. Operational measurements, service journals and recovery manifests remain in the private project evidence, not a public GitHub publication.
