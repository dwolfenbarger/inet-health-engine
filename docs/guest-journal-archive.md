# Guest-owned INET journal archival

CT553 (inet-health-prod / prod553) and CT554 (inet-health-uat / uat554) run their own native systemd archive service every five minutes while online. PostgreSQL, hot journals and deduplication keys stay on each guest's root disk. The worker uses local PostgreSQL peer authentication as postgres; no SSH, Proxmox API token, provider credential or host dispatcher is required.

## Storage and permissions

Reuse the existing global NAS1 CIFS share on every eligible Proxmox node. A native LXC bind attaches /Volumes/NAS1/storage/ai/ihealth_storage-recovery-20261101 to /var/lib/inet-archive-private/nas with shared=1,backup=0. Native pve-container@553/554 dependencies require /Volumes/NAS1 before startup. There is no new host CIFS mount.

The private parent is postgres-owned mode 0700. Only this service receives supplementary GID1000 for NAS writes; the public david app account cannot traverse the private parent. The service has CPUQuota=100%, MemoryMax=512M, idle I/O scheduling and a one-hour timeout. NAS source, mountpoint, logical storage identity, quotas and free-space reserve are checked before publication. Missing mounts fail without a local-directory fallback. A shared NAS flock serializes both publishers.

The bind is excluded from vzdump/PBS. These verified archives are not proof of an independent backup.

## Retention and integrity

Keep the existing 24-hour local / 72-hour raw policy, 8GiB hot and dedup budgets, 350GiB namespace cap, 260GiB admission, 120GiB detail budget and 90GiB shared free-space reserve. A cycle processes up to four sealed segments; --batches can explicitly bound recovery at 1–12 cycles.

Each export is copied and fsynced, verified for compressed and decoded hashes, record counts, segment identity and source hostname, then atomically published and verified again. Only then does the existing maintenance helper acknowledge and, when older than 24 hours, retire the local segment. Existing database retirement guards are unchanged. Catalogue and routing analysis remain on NAS. Completed local staging cleanup remains bounded by the existing helper.

Pins at pins/<namespace>/<segment> or pins/<segment> prevent 72-hour raw expiry. Recovery pins are deliberate holds pending operator review, not an automatic future deletion task.

## 2026-11-01 recovery boundary

The operator confirmed accidental deletion of the original ihealth_storage archive. Exposed NAS recycle/snapshot locations and a hidden local mount underlay did not yield a copy; NAS admin access was unavailable, so NAS-side snapshots are not ruled out. The replacement namespace has a new generation marker and explicitly records that old archive continuity was not recovered.

Initial retained production and UAT segments were pinned. During the first UAT pilot, normal expiry removed routing_messages_p2026102501 before its recovery pin existed. Its retained local export restored all 68,053 records; hashes were reverified, its catalogue repaired, and its pin added. Recovery tests now explicitly cover pinned old raw archives.

CT554 was started only for verification and is to return to stopped/onboot=0. The worker is enabled for future starts. Do not start this guest merely to refresh archive status.

## Validation and operation

Run python3 -m unittest discover -s /opt/inet-journal-runtime/tests -p test_guest_journal_archive.py. The five tests cover mount identity, corruption refusal before acknowledgement/retirement, source mismatch, acknowledgement-before-retirement order, and recovery-pin preservation.

Use rotate-journal-guest.py --check under User=postgres and SupplementaryGroups=1000 for read-only validation. Review journalctl -u inet-journal-archive and existing journal-maintenance.py status output. Prove collector freshness separately; a healthy API alone does not prove live ingestion.

Deploy new scripts alongside the existing journal-maintenance.py, vendored dependencies and collectors modules. Verify systemd units before enablement. Keep the old host dispatcher until production publication, retirement and collector resumption are verified. Archive its exact files before removal. Any later rollback must explicitly reconcile the new namespace and receipts; do not blindly re-enable the old missing-path dispatcher.
