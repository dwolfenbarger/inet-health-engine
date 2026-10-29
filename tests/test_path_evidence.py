import importlib.util
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone

spec=importlib.util.spec_from_file_location('path_evidence',Path(__file__).resolve().parents[1]/'api/api/path_evidence.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


class PathEvidence(unittest.TestCase):
 def test_direction_and_origin(self):
  x=p.as_segment([64500,64501,64502,64503],64501,64503)
  self.assertTrue(x['ordered']);self.assertEqual(x['transit_asns'],[64502]);self.assertEqual(x['nodes'][0]['asn'],64501)
  self.assertEqual(p.as_segment([64500,64501,64502,64503],64503,64501)['state'],'origin_mismatch')
 def test_prepend_kept(self):
  x=p.as_segment([64500,64500,64501,64502,64502],64500,64502)
  self.assertEqual(x['raw_path'],[64500,64500,64501,64502,64502]);self.assertEqual(x['nodes'][0]['repetitions'],2)
  self.assertEqual(x['nodes'][-1]['repetitions'],2);self.assertTrue(x['ordered'])
 def test_sets_and_confederations_not_linearized(self):
  for raw in [[64500,[64501,64502],64503],[64500,{'confederation':'(64501 64502)'},64503]]:
   x=p.as_segment(raw,64500,64503);self.assertFalse(x['ordered']);self.assertEqual(x['state'],'ambiguous_segment')
  self.assertEqual(p.as_segment([64500,[64501,64502],64503],64501,64503)['state'],'source_not_ordered')
 def test_no_synthetic_peer_or_reversed_path(self):
  self.assertEqual(p.as_segment([64501,64503],64500,64503)['state'],'source_not_observed')
 def test_loops(self):
  self.assertEqual(p.as_segment([1,2,1,3],1,3)['state'],'source_loop_ambiguous')
  self.assertEqual(p.as_segment([1,2,4,2,3],1,3)['state'],'path_loop_ambiguous')
 def test_four_byte_and_unknown(self):
  self.assertEqual(p.as_segment([4294967295,133210],4294967295,133210)['nodes'][0]['asn'],4294967295)
  for a in [0,True,-1,4294967296,'64500']:
   self.assertEqual(p.path_nodes([a])[0]['kind'],'unknown')
  self.assertEqual(p.as_segment({'unparsed':'broken'},1,2)['state'],'origin_mismatch')
 def test_same_as(self):
  self.assertTrue(p.as_segment([1,2,3],3,3)['ordered']);self.assertEqual(p.as_segment([1,2,3],3,3)['transit_asns'],[])
 def test_withdrawal_correlation_not_cross_prefix(self):
  value={'facts':{'withdrawn_prefix_paths':[{'prefix':'198.18.0.0/24','as_path':[1,2,3]},{'prefix':'198.18.1.0/24','as_path':[1,4,5]}]}}
  self.assertEqual(p.evidence_pairs(value,2,5),[])
  self.assertEqual(p.evidence_pairs(value,4,5)[0]['prefix'],'198.18.1.0/24')
 def test_victim_before_path_survives_origin_change(self):
  x={'before':{'present':True,'path':[1,2,3]},'after':{'present':True,'path':[4,5]}}
  self.assertEqual([v['side'] for v in p.evidence_pairs(x,2,3)],['before'])


class TraceEvidence(unittest.TestCase):
 def setUp(self):
  self.now=datetime.now(timezone.utc);self.lower=self.now-timedelta(hours=1)
  self.meta={'id':123,'af':4,'target_ip':'8.8.8.8'};self.probe={'id':5}
  self.raw={'msm_id':123,'prb_id':5,'af':4,'type':'traceroute','timestamp':self.now.timestamp()-1,'dst_addr':'8.8.8.8','proto':'ICMP',
            'result':[{'hop':1,'result':[{'from':'192.168.1.1','rtt':0.0},{'x':'*'}]},
                      {'hop':3,'result':[{'from':'8.8.8.8','rtt':15},{'from':'8.8.4.4','rtt':16},{'from':'8.8.8.8','rtt':17}]}]}
 def parse(self,raw=None):return p.trace_observation(raw or self.raw,self.meta,self.probe,self.lower,self.now)
 def test_preserves_ttls_unknowns_ecmp_and_zero_rtt(self):
  v=self.parse();self.assertEqual([h['ttl'] for h in v['hops']],[1,2,3]);self.assertEqual(v['hops'][1]['state'],'not_recorded')
  self.assertIsNone(v['hops'][1]['timeouts']);self.assertEqual(v['hops'][0]['timeouts'],1)
  self.assertEqual(v['hops'][0]['responders'][0]['median_rtt_ms'],0)
  self.assertEqual(len(v['hops'][2]['responders']),2);self.assertTrue(v['destination_replied'])
 def test_identity_target_and_time_refusal(self):
  for change in [{'msm_id':999},{'prb_id':6},{'af':6},{'type':'ping'},{'dst_addr':'1.1.1.1'},
                 {'timestamp':self.now.timestamp()+1},{'timestamp':self.lower.timestamp()-1},{'timestamp':True}]:
   with self.subTest(change=change),self.assertRaises(ValueError):self.parse(self.raw|change)
 def test_invalid_duplicate_or_unbounded_hops(self):
  for hops in [[{'hop':1,'result':[]},{'hop':1,'result':[]}],[{'hop':256}], [{'hop':True}], [{'hop':1,'result':[{}]*33}]]:
   with self.assertRaises(ValueError):self.parse(self.raw|{'result':hops})
 def test_invalid_rtt_is_not_zero(self):
  for rtt in [True,float('nan'),float('inf'),-1]:
   with self.assertRaises(ValueError):self.parse(self.raw|{'result':[{'hop':1,'result':[{'from':'1.1.1.1','rtt':rtt}]}]})
 def test_timeout_comparison_is_indeterminate(self):
  x=self.parse();r=p.compare_traces(x,x);self.assertEqual(r['state'],'incomplete_comparison');self.assertEqual(r['unknown_ttls'],[2])
 def test_changed_responders_are_observations_not_confirmed_route_change(self):
  x=self.parse();y=self.parse(self.raw|{'result':[{'hop':1,'result':[{'from':'1.1.1.1','rtt':1}]}]})
  self.assertEqual(p.compare_traces(x,y)['changed_ttls'],[1])
 def test_ipv6(self):
  self.meta.update(af=6,target_ip='2001:4860:4860::8888')
  x=self.raw|{'af':6,'dst_addr':'2001:4860:4860::8888','result':[{'hop':1,'result':[{'from':'2001:4860:4860::8888','rtt':5}]}]}
  self.assertTrue(self.parse(x)['destination_replied'])
 def test_ping_packet_loss_and_no_fake_rtt(self):
  raw=self.raw|{'type':'ping','sent':3,'rcvd':0,'avg':-1}
  x=p.ping_observation(raw,self.meta,self.probe,self.lower,self.now);self.assertEqual(x['loss_percent'],100);self.assertIsNone(x['average_rtt_ms'])
  raw.update(rcvd=3,avg=0);self.assertEqual(p.ping_observation(raw,self.meta,self.probe,self.lower,self.now)['average_rtt_ms'],0)
  raw.update(rcvd=4)
  with self.assertRaises(ValueError):p.ping_observation(raw,self.meta,self.probe,self.lower,self.now)


if __name__=='__main__':unittest.main()
