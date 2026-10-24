"""Operational issue boundaries: provenance, scope, snapshot continuity and failures."""
import json,unittest,uuid
from datetime import datetime,timezone,timedelta
from ipaddress import ip_network,ip_address
from unittest.mock import AsyncMock,patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routes_issues import router,build_snapshot,prefix_matches

class IssueContracts(unittest.TestCase):
 def test_prefix_scope_includes_more_specifics_but_not_unrelated_or_malformed(self):
  scope=ip_network('192.0.2.0/24')
  self.assertTrue(prefix_matches('192.0.2.128/25',scope))
  for value in ['192.0.0.0/16','2001:db8::/32','None',None]:self.assertFalse(prefix_matches(value,scope))
  self.assertTrue(prefix_matches('2001:db8::/32',ip_address('2001:db8::1')))
 def test_invalid_and_expired_scope_never_returns_empty_success(self):
  app=FastAPI();app.include_router(router);cache=AsyncMock();cache.get.return_value=None
  with TestClient(app) as c,patch('api.routes_issues.get_redis',AsyncMock(return_value=cache)):
   for query in ['resource=bogus','asn=4294967296','collector=bogus','minutes=1441','family=7']:
    self.assertEqual(c.get('/issues?'+query).status_code,422)
   self.assertEqual(c.get('/issues?snapshot_id='+str(uuid.uuid4())).status_code,409)
 def test_snapshot_pages_are_pinned_and_filter_changes_rejected(self):
  app=FastAPI();app.include_router(router);saved={};cache=AsyncMock()
  async def put(k,ttl,v):saved[k]=v
  async def get(k):return saved.get(k)
  cache.setex.side_effect=put;cache.get.side_effect=get
  scope={'minutes':60,'asn':None,'resource':None,'family':'all','collector':'all'}
  source={'items':[{'id':str(i),'kind':'bgp_flap'} for i in range(3)],'scope':scope,'summary':{'bgp_flap':3}}
  with TestClient(app) as c,patch('api.routes_issues.get_redis',AsyncMock(return_value=cache)),patch('api.routes_issues.get_pg_pool',AsyncMock()),patch('api.routes_issues.build_snapshot',AsyncMock(return_value=source)) as build:
   first=c.get('/issues?limit=2').json();sid=first['snapshot_id']
   second=c.get('/issues?snapshot_id='+sid+'&offset=2&limit=2').json()
   self.assertEqual([i['id'] for i in first['items']+second['items']],['0','1','2']);self.assertIsNone(second['next_offset']);self.assertEqual(build.await_count,1)
   self.assertEqual(c.get('/issues?snapshot_id='+sid+'&asn=13335').status_code,422)
 def test_store_failure_is_unavailable_without_private_error(self):
  app=FastAPI();app.include_router(router)
  with TestClient(app) as c,patch('api.routes_issues.get_redis',AsyncMock(side_effect=RuntimeError('private connection string'))):
   r=c.get('/issues');self.assertEqual(r.status_code,503);self.assertNotIn('private',r.text)

class EvidenceContracts(unittest.IsolatedAsyncioTestCase):
 async def test_victim_transit_and_peer_scope_and_report_count_are_preserved(self):
  now=datetime.now(timezone.utc);pool=AsyncMock();pool.fetchval.return_value=now
  pool.fetch.return_value=[{'event_type':'bgp_hijack','affected_prefix':'192.0.2.0/24','origin_asn':64501,'expected_asn':64502,'first_seen':now,'last_seen':now,'reports':28,'severity':4,'detector_confidence':.8,'viewpoints':1,'sources':['ris/rrc00'],'latest_id':str(uuid.uuid4())}]
  with patch('api.routes_issues.mesh_data',AsyncMock(return_value={'directions':[]})):
   r=await build_snapshot(pool,60,64502,None,'all','all')
  sql,args=pool.fetch.call_args.args[0],pool.fetch.call_args.args[1:]
  self.assertIn('expected_asn=$4',sql);self.assertIn("raw_data->'as_path'",sql);self.assertIn("raw_data->>'peer_asn'",sql);self.assertEqual(args[3],64502)
  self.assertEqual(r['items'][0]['reports'],28);self.assertNotIn('transitions',r['items'][0]);self.assertEqual(r['summary']['bgp_hijack'],1)
 async def test_measured_candidates_keep_stale_state_and_do_not_claim_prefix_scope(self):
  pool=AsyncMock();pool.fetch.return_value=[];pool.fetchval.return_value=None
  d={'pair':{'id':'a-b-v4','family':4},'source':{'as_v4':64500},'target_asn':64501,'candidates':['latency_increase'],'observed_at':(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(),'collection_state':'stale'}
  with patch('api.routes_issues.mesh_data',AsyncMock(return_value={'directions':[d]})):
   r=await build_snapshot(pool,60,None,None,'all','all');scoped=await build_snapshot(pool,60,None,ip_network('192.0.2.0/24'),'all','all')
  self.assertEqual(r['items'][0]['state'],'stale');self.assertIsNone(r['items'][0]['severity']);self.assertEqual(r['routing_input']['state'],'unavailable');self.assertEqual(scoped['items'],[])
 async def test_measurement_failure_preserves_routing_with_explicit_degradation(self):
  pool=AsyncMock();pool.fetch.return_value=[];pool.fetchval.return_value=None
  with patch('api.routes_issues.mesh_data',AsyncMock(side_effect=RuntimeError('private'))):r=await build_snapshot(pool,60,None,None,'all','all')
  self.assertIsNotNone(r['performance_error']);self.assertNotIn('private',r['performance_error'])
 async def test_detail_is_exact_observation_and_never_invents_prior_path(self):
  from api.routes_issues import detail
  now=datetime.now(timezone.utc);eid=uuid.uuid4();pool=AsyncMock()
  pool.fetchrow.side_effect=[{'time':now,'event_id':str(eid),'event_type':'bgp_hijack','affected_prefix':'192.0.2.0/24','origin_asn':64501,'expected_asn':64502,'source':'ris/rrc00','raw_data':json.dumps({'peer_asn':64503,'as_path':[64503,64501]})},None]
  pool.fetch.return_value=[]
  with patch('api.routes_issues.get_pg_pool',AsyncMock(return_value=pool)):r=await detail(eid,now)
  self.assertIsNone(r['previous_origin_announcement']);self.assertIsNone(r['current_origin_validation']);self.assertEqual(r['candidate']['raw_data']['as_path'],[64503,64501])
  args=pool.fetch.call_args.args;self.assertEqual(args[-2:],('rrc00',64503));self.assertEqual(pool.fetchrow.call_args_list[0].args[1:],(now,eid))
