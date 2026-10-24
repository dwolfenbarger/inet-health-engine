# Live collection and journal operations — 2026-10-24

Prepared by OpenAI Codex. David directed that backup coverage be held and work focus on an application NAS quota, existing collection/validator function, journal rotation and resuming continuous RIS. Backup coverage is no longer a gate for this operating policy. No fleet backup job is changed by this work.

## Operating policy

The NAS project has one combined **350-GiB application limit**, with admission stopped at **260 GiB** to preserve **90 GiB** of reserve. Detailed archives share a **120-GiB** bucket. Managed publishers serialize admission, publication and expiry under a host-local lock; check the actual CIFS mount and storage identity before writing; include pending files in usage; and refuse ordinary-directory fallback. This is an application quota, not a claimed filesystem/server quota. Container allocations remain 200 GiB PROD /100 GiB UAT; the requested 150-GiB operating budget does not shrink them.

Operational journals retain at least **24 hours locally** and **72 hours in the private development archive**. Local payload storage and deduplication identities each have an **8-GiB** ceiling; receiver intake still pauses below **20 GiB** root free space. The receiver is enabled at boot and retries a capacity pause, while an explicit systemctl stop remains an operator stop. A five-minute host timer pre-creates future hourly partitions, publishes sealed segments, verifies readback, retires eligible local partitions and applies the declared NAS raw window. Current routes, epochs/generations, checkpoint counters, gaps and MRT audit metadata are retained independently.

Partitions use a database-controlled storage clock, so a delayed frame cannot reopen an exported old partition. Global message identities survive payload rotation. Identities expire only behind a 72-hour horizon and archived segment boundaries. A frame older than that floor is retained as audit evidence and opens a gap; an old OPEN cannot reset the current epoch. Missing/duplicate input never silently advances a checkpoint.

Operational manifests have a distinct journal schema. Local retirement additionally requires a matching NAS identity, verified object/decoded checksums, observer counts/sequence bounds, an explicit eligible receipt and the protected 24-hour age. Original pilot profiles keep their non-expiry promise. The two initial journal publications made before this schema distinction remain preserved; new operational publications use the v2 subdirectory. NAS raw expiry requires completed local retirement and honors pins. Per-segment activity contributions and catalogue receipts survive raw expiry; frame counts are not health scores or affected-network counts.

Full retained data remains available only in private development namespaces. Production can supply sealed operational partitions to this designated development publisher; production has no NAS mount or public bulk archive API. The publisher records the source environment and uses separate uat554/prod553 namespaces under the same combined quota. This avoids treating the physical source hostname as the product's archive-access policy. Public application endpoints continue to return bounded live/investigation evidence.

## Collection and validator repairs

The intermittent legacy RIS sample now admits one active cycle, writes in 500-row batches and caps a window at 10,000 updates. It remains a sample across five collectors; it does not replace selected-peer continuous state. The previous 25,000-row calls exceeded native deadlines, and overlapping cycles compounded the work. Provider evidence uses bounded calls inside the same atomic cycle, preserving rollback when a later batch fails.

Development history capture no longer rewrites one MVCC budget row for every accepted record. Conservative sequence credits reserve bytes/records under the capture lock; the policy mirror updates once at commit. Aborted transactions may consume credits, so capacity accounting is conservative rather than an exact retained-row count. Busy capture records a minimum gap marker instead of blocking a different live provider. Repeated skipped records in one transaction do not create a long hot-update chain. History remains a finite, separately bounded development qualification.

Routinator's /json export in the installed version timestamps validation start; its validity responses/status identify completed validation. A 21–23-minute run was therefore being rejected by the old 20-minute generatedTime test immediately after completion. Bulk validation now checks a coherent serial before/after export and preserves both start and completion. The published freshness ceiling is **60 minutes from completed validation**, covering the measured validation/refresh cadence; future or stale clocks remain rejected. Redis expiry and API cache eligibility use the same ceiling.

Candidate selection bounds the latest 20,000 observed updates before DISTINCT, avoiding an aggregate over millions of historical rows. This is a declared observed cohort, not validation of every global prefix. Focused prefix/IP/ASN route investigations also request at most eight missing origin validations directly from the local validator. Returned prefix/origin identity and timestamp must match; stale, malformed or unavailable results stay unknown. RPKI origin validity never becomes an AS-path safety or outage conclusion.

## Verified development operation

Continuous RIS resumed at **2026-10-24 00:27:37 UTC**. The four selected observers connected and their counters/event timestamps advanced without a receiver restart during the subsequent check. All 1,828,156 original identities and payloads were preserved through the switch; bootstrap storage was approximately 2.15 GB and dedup storage 789 MB. The original payload table remains protected by the local age window.

Real candidate-source checks completed:

| Check | Result |
|---|---|
| Public Atlas | 35,756 results retained; 122.28 seconds; no collection error |
| Sampled BGP | 10,000 updates and six heuristic candidates; 57.5 seconds |
| Local RPKI | 6,263 pairs: 5,171 valid /55 invalid /1,037 not-found; 68.78 seconds |
| NAS publication | Full bootstrap journal readback verified; subsequent maintenance passes succeeded |
| Regression tests | 81 unit tests; 27 journal/writer database checks; 24 history accounting checks |

The previous seven-hour interruption remains recorded. Strict MRT replay continued through approximately 19:25–19:45 UTC of the missing interval at the 00:58 UTC check. Fresh reception and repaired historical continuity are separate conditions; the UI must keep showing gaps until reconstruction is complete. No accelerated RIB rewrite or invented historical repair is used.

## Production rollout and provenance

Production previously had native sampled collectors and provider evidence, but no maintained-route tables/receiver. A consistent seed containing current routes, observer/session state, gaps, MRT audit records and dedup identities was prepared from development. It contains no raw journal. SHA256: `7e16c30b45801a4e4efbf9f8ab774d55c522d38b4aadc5ba962a743ec5469baf`.

The production restore refuses existing target tables, verifies the checksum, restores in one transaction, preserves gaps and records seed provenance. A new receiver start adds its own interruption boundary. Receiver and recovery state are independent after seeding. Production promotion and endpoint evidence are recorded in the ledger and the deployment checkpoint below.

## Remaining product scope

These repairs make the implemented live path operable. They do not finish the whole global-NOC charter: observed viewpoint/AFI diversity, independent RIB reconciliation, incident correlation and lifecycle, congestion attribution, transit-AS search, full annual analytical cardinality and historical UX remain work. There is no defensible global coverage percentage or all-Internet health score. A completed API call or an empty source result is not an Internet all-clear.


## Deployment checkpoint — 2026-10-24 01:49 UTC

Both native application trees now run **c8c108fb9510e986e7d686ca6955b0c05705c48f**. UAT promotion completed 01:31:12 UTC; production 01:35:29 UTC. Workers were drained with beat stopped and active/reserved/scheduled counts zero. Production rollback is `/srv/inet-health-backups/20261024T013524Z`; UAT rollback is `/srv/inet-health-backups/20261024T013107Z`. The tested frontend archive SHA256 is `8971b4d6c918e46352a7155e8bbdcf9e5abe249bf3181e8c84dd937ed1db43ee`.

Production's consistent current-state seed restored successfully at 01:17:52 UTC. Its independently running RIS receiver began at **01:39:42 UTC**, with four peers connected, advancing event/counter checks and zero restarts. Native API, worker and beat are active. MRT recovery initially exposed a missing native dependency; installing the same `pybgpkit==0.9.0` /`pybgpkit-parser==0.18.0` pair used in UAT fixed imports, `pip check` passed, and actual RIS/RouteViews archives then applied. The optional native routing dependency file and installer import preflight now make this prerequisite explicit. Neither receiver restart nor a forced recovery stop was needed.

At the 01:46 UTC API check through nginx, production sampled RIS was fresh, RPKI successfully validated 8,428 candidate pairs, Atlas retained 35,733 latest probe results, and the mesh had all 28 directed paths/baselines (27 classified observations_available at that instant; classifications may include change candidates). IODA returned no event candidates. Radar remains unconfigured and M-Lab unavailable. Partial source state describes bounded scope. IP lookup for `1.1.1.1` returned three observed `1.1.1.0/24` paths to AS13335 with fresh local origin validity. Coverage, snapshot, mesh and maintained-route endpoints answered successfully. No global score or global coverage percentage is invented.

The private host publisher completed maintenance for both source namespaces under the shared quota, and its five-minute timer is enabled and active. Latest measured project use was 242,875,780 bytes including the original non-expiring pilot objects; operational v2 publication/readback receipts were verified for both sources. Initial production sealed segments were empty because the receiver started in the current hour. Nonempty closed production segments will be admitted by the same timer once eligible. Actual age-based retirement has not occurred yet: the protected 24-hour age has not elapsed; refusal/retirement behavior was exercised in disposable database tests. Existing sampled BGP/provider tables are not covered by journal partition expiry; their broader retention remains separate work.

Production has about 151 GiB free on its existing root, UAT 58 GiB. UAT hot payload was approximately 2.26 GiB /dedup 0.76 GiB; production was approximately 21 MiB /dedup 0.41 GiB in the last maintenance sample. The new receiver budgets leave current intake room but do not establish a long-term storage forecast.

UAT replay had repaired New York and Singapore continuity at the latest check; Sao Paulo and Paris still had explicit pending gaps. Production inherited the seed's recorded gaps and adds its independent startup boundary; replay was advancing through the interrupted period and must complete before continuity is shown. RouteViews applied updates through at least 01:15 UTC while catching up. These statuses are time-specific, not completion claims.

Browser visual/accessibility review remains blocked: the connected extension was unavailable and the cloud browser reported `ERR_CERT_COMMON_NAME_INVALID` when opening the LAN production URL. No security warning was bypassed. The deployed frontend contains the maintained lookup, coverage/mesh panel and synchronized 2D/3D controls; rendered interaction QA is not claimed by API checks. On the LAN, open `http://192.168.1.113/`, use **Maintained views** for prefix/IP evidence, and the **Evidence coverage**/measurement panels for scope and baselines.
