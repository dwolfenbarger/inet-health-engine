"""Regressions for live data integrity and unknown coverage semantics."""
import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from api.routes_noc import canonical_prefix, freshness
from api.routes_noc import router
from collectors.ripe_ris_collector import parse_ris_message, write_updates_batched
from collectors.bgp_collector import detect_flaps, _prefix_flap_tracker, FLAP_THRESHOLD_COUNT
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


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

    def test_peer_address_is_not_reported_as_the_forwarding_next_hop(self):
        rows=parse_ris_message({'data':{'type':'UPDATE','path':[174,13335],
            'peer':'192.0.2.1','host':'rrc00.ripe.net',
            'announcements':[{'next_hop':'192.0.2.2','prefixes':['1.1.1.0/24']}],
            'withdrawals':['9.9.9.0/24']}})
        self.assertEqual(rows[0].next_hop,'192.0.2.2')
        self.assertIsNone(rows[1].next_hop)

    def test_failed_storage_cannot_report_success(self):
        class Broken:
            def acquire(self):
                raise RuntimeError('intentional failure')
        with self.assertRaisesRegex(RuntimeError,'intentional failure'):
            asyncio.run(write_updates_batched(Broken(),[object()]))

    def test_repeated_announcements_and_other_peers_are_not_flaps(self):
        _prefix_flap_tracker.clear()
        row=parse_ris_message({'data':{'type':'UPDATE','path':[174,13335],'peer_asn':174,'host':'rrc00.ripe.net','announcements':[{'prefixes':['1.1.1.0/24']}]}})[0]
        for _ in range(FLAP_THRESHOLD_COUNT+5):
            self.assertEqual(detect_flaps(row),[])
        self.assertEqual(detect_flaps(row.model_copy(update={'peer_asn':3356,'change_type':'withdraw'})),[])
        result=[]
        for i in range(FLAP_THRESHOLD_COUNT):
            result=detect_flaps(row.model_copy(update={'change_type':'withdraw' if i%2==0 else 'announce'}))
        self.assertEqual(len(result),1)

    def test_api_empty_store_is_unknown_and_failures_are_explicit(self):
        class Empty:
            async def fetch(self,*args):return []
            async def fetchval(self,*args):return None
        async def empty():return Empty()
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client, patch('api.routes_noc.get_pg_pool',empty):
            result=client.get('/noc/snapshot').json()
            self.assertIsNone(result['health_score'])
            self.assertEqual(result['source_state']['state'],'unavailable')
            self.assertEqual(client.get('/noc/snapshot?collector=invalid').status_code,422)
            self.assertEqual(client.get('/noc/snapshot?minutes=1000').status_code,422)
        async def broken():raise RuntimeError('private database address')
        with TestClient(app) as client, patch('api.routes_noc.get_pg_pool',broken):
            result=client.get('/noc/snapshot')
            self.assertEqual(result.status_code,503)
            self.assertNotIn('private database address',result.text)


if __name__=='__main__':
    unittest.main()
