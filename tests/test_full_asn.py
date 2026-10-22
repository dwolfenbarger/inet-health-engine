import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routes_noc import router
from collectors.ripe_ris_collector import parse_ris_message


class FullASNTests(unittest.TestCase):
    def test_origin_peer_and_repeated_path_keep_full_range(self):
        rows=parse_ris_message({'data':{'type':'UPDATE','path':[4288000245,4294967295,4294967295],
            'peer_asn':4288000245,'host':'rrc00.ripe.net','announcements':[{'prefixes':['203.0.113.0/24']}]}})
        self.assertEqual(rows[0].origin_asn,4294967295)
        self.assertEqual(rows[0].peer_asn,4288000245)
        self.assertEqual(rows[0].as_path,[4288000245,4294967295,4294967295])

    def test_api_binds_large_asn_without_replacing_it(self):
        calls=[]
        class Pool:
            async def fetch(self,sql,*args):calls.append((sql,args));return []
            async def fetchval(self,*args):return datetime.now(timezone.utc)
        async def get_pool():return Pool()
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client,patch('api.routes_noc.get_pg_pool',get_pool):
            response=client.get('/noc/snapshot?asn=4288000245')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['scope']['asn'],4288000245)
            self.assertEqual(calls[0][1][2],4288000245)
            self.assertEqual(calls[1][1][1],4288000245)
            self.assertEqual(client.get('/noc/snapshot?asn=4294967296').status_code,422)
