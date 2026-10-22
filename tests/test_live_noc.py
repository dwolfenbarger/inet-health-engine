"""Regressions for live data integrity and unknown coverage semantics."""
import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from api.routes_noc import canonical_prefix, freshness
from collectors.ripe_ris_collector import parse_ris_message, write_updates_batched
from fastapi import HTTPException


class LiveIntegrityTests(unittest.TestCase):
    def test_silence_is_not_health(self):
        now=datetime.now(timezone.utc)
        self.assertEqual(freshness(None,now)['state'],'unavailable')
        self.assertEqual(freshness(now-timedelta(minutes=10),now)['state'],'stale')
        self.assertEqual(freshness(now-timedelta(seconds=20),now)['state'],'fresh')

    def test_prefix_is_canonical_and_invalid_input_rejected(self):
        self.assertEqual(canonical_prefix('2001:db8:0:0::1/48'),'2001:db8::/48')
        self.assertEqual(canonical_prefix('1.1.1.9/24'),'1.1.1.0/24')
        with self.assertRaises(HTTPException):
            canonical_prefix("';DROP TABLE bgp_updates;--")

    def test_prepend_and_nonconsecutive_loop_are_preserved(self):
        rows=parse_ris_message({'data':{'type':'UPDATE','path':[174,174,3356,174,13335],'peer_asn':174,'host':'rrc00.ripe.net','announcements':[{'prefixes':['1.1.1.0/24']}]}})
        self.assertEqual(rows[0].as_path,[174,174,3356,174,13335])

    def test_as_set_does_not_become_a_fabricated_sequence(self):
        rows=parse_ris_message({'data':{'type':'UPDATE','path':[174,[3356,13335]],'host':'rrc00.ripe.net','announcements':[{'prefixes':['1.1.1.0/24']}]}})
        self.assertEqual(rows[0].as_path,[])
        self.assertIsNone(rows[0].origin_asn)

    def test_failed_storage_cannot_report_success(self):
        class Broken:
            def acquire(self):
                raise RuntimeError('intentional failure')
        with self.assertRaisesRegex(RuntimeError,'intentional failure'):
            asyncio.run(write_updates_batched(Broken(),[object()]))


if __name__=='__main__':
    unittest.main()
