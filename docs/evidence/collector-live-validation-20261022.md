# Bounded live collector validation — 2026-10-22

OpenAI Codex validated the da6a6f8 collector-loop candidate on inet-health-dev1 using isolated candidate source, one RIS collector (rrc00) and two consecutive8-second source windows on the same persistent task loop. Each cycle was bounded to24seconds.

| Cycle | Rows before | Rows after | New source rows | Latest source timestamp UTC |
| --- | ---: | ---: | ---: | --- |
|1|0|7721|7721|2026-10-22T15:58:16.150Z|
|2|7721|14822|7101|2026-10-22T15:58:35.170Z|

Both cycles persisted new BGP observations, source timestamps advanced and the shared pool closed cleanly. Logs contained no batch-write/event-loop errors. This test wrote real observations into DEV1; it was not read-only. Existing live services and their source/venv were not replaced or restarted.

DEV1 lacks tenacity in its runtime venv; the already staged pinned8.4.2 package under /tmp/inet-review-20261022/test-deps supplied this test dependency. Source was /tmp/inet-review-20261022, with runtime configuration loaded privately without logging credentials.

Evidence: Vengeance shell job46371ed9-e06a-46c5-8a66-ee74cdaba322. Helper SHA256b19ddd02830aa46ca9b611a23dfad1f82c8dde4e56d7801fc470ca6f30815877.

This proves the bounded repeated collector path through the persistent-loop bridge. It does not prove a restarted Celery service, all scheduled tasks, five-collector load, sustained recovery or detector accuracy. No production deployment or merge was performed.
