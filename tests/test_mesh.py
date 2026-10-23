import unittest,math
from datetime import datetime,timedelta,timezone
from collectors.atlas_mesh import samples,summarize,check_measurement
from api.routes_coverage import mesh_state
UTC=timezone.utc
class MeshTests(unittest.TestCase):
 def setUp(self):self.now=datetime(2026,10,23,8,tzinfo=UTC);self.pair={'measurement_id':12,'source_probe':7,'family':4}
 def data(self,rows):return {'msm_id':12,'probe_id':7,'type':'ping','resolution':'none','data':rows}
 def point(self,minutes,sent=3,received=3,rtt=10):return {'timestamp':(self.now-timedelta(minutes=minutes)).timestamp(),'sent':sent,'received':received,'rtt_med':rtt}
 def test_total_loss_is_not_zero_latency(self):
  row=samples(self.data([self.point(1,received=0,rtt=-1)]),self.pair,self.now)[0]
  self.assertIsNone(row['rtt']);s=summarize([row],self.now);self.assertEqual(s['current']['loss_percent'],100);self.assertIsNone(s['current']['median_of_sample_medians_ms'])
 def test_bad_identity_counter_future_and_rtt_refuse_evidence(self):
  for d in [self.data([self.point(1,received=4)]),self.data([self.point(-10)]),self.data([self.point(1,rtt=float('nan'))]),{**self.data([]),'probe_id':8}]:
   with self.assertRaises(ValueError):samples(d,self.pair,self.now)
 def test_baseline_excludes_current_and_detects_change_without_health_score(self):
  raw=[self.point(m) for m in range(20,1000,4)]+[self.point(m,rtt=40) for m in [1,5,9]]
  s=summarize(samples(self.data(raw),self.pair,self.now),self.now)
  self.assertTrue(s['baseline_established']);self.assertEqual(s['delta_ms'],30);self.assertEqual(s['candidates'],['increased_latency']);self.assertIsNone(s['health_score'])
 def test_missing_or_short_baseline_does_not_become_healthy(self):
  for rows in [[],samples(self.data([self.point(1)]),self.pair,self.now)]:
   s=summarize(rows,self.now);self.assertEqual(s['state'],'insufficient_recent_samples');self.assertFalse(s['baseline_established'])
 def test_stale_or_failed_collection_overrides_change_state(self):
  p={'observed_at':(self.now-timedelta(hours=2)).isoformat(),'state':'measurement_change_candidate'}
  self.assertEqual(mesh_state(p,self.now),'stale');self.assertEqual(mesh_state(p,self.now,True),'collection_error')
 def test_metadata_requires_actual_mesh_target(self):
  meta={'id':12,'af':4,'type':'ping','is_public':True,'tags':['anchoring','mesh'],'status':{'id':2},'target_ip':'192.0.2.1'}
  check_measurement(meta,self.pair,{'ip_v4':'192.0.2.1'})
  with self.assertRaises(ValueError):check_measurement(meta,self.pair,{'ip_v4':'192.0.2.2'})
