import sys,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api'))
from api.incident_evidence import compact_event,scoped_event,page_impact,measurement_match,change_summary,coverage_action

class EvidenceTests(unittest.TestCase):
 def event(self,kind='path_change',proof=None):
  return {'event_id':'00000000-0000-0000-0000-000000000001','observer':'a','prefix':'8.8.8.0/24','kind':kind,'candidate':None,'evidence':proof or {'before':{'present':True,'path':[1,2,3]},'after':{'present':True,'path':[1,4,3]}}}
 def test_transit_transition(self):
  c=compact_event(self.event());self.assertEqual(c['change']['appeared_asns'],[4]);self.assertEqual(c['change']['disappeared_asns'],[2]);self.assertEqual(c['change']['before_path'],[1,2,3])
 def test_prepends_not_new_asns(self):
  c=change_summary({'before':{'present':True,'path':[1,1,3]},'after':{'present':True,'path':[1,1,1,3]}});self.assertTrue(c['path_changed']);self.assertFalse(c['appeared_asns']);self.assertFalse(c['disappeared_asns'])
 def test_baseline_not_change(self):
  p=page_impact([compact_event(self.event('baseline'))]);self.assertEqual(p['observations'],1);self.assertEqual(p['recorded_changes'],0)
 def test_gaps_not_changes(self):
  for kind in ['gap','reset','state_expired']:self.assertEqual(page_impact([compact_event(self.event(kind))])['recorded_changes'],0)
 def test_origin_victim_path_preserved(self):
  e=self.event(proof={'before':{'present':True,'path':[10,20,30]},'after':{'present':True,'path':[10,31]}});e['candidate']='bgp_hijack';x=compact_event(e);self.assertEqual(x['change']['disappeared_asns'],[20,30]);self.assertEqual(page_impact([x])['candidate_flags'],{'bgp_hijack':1})
 def test_unknown_paths_remain_unknown(self):
  x=compact_event(self.event(proof={'before':None,'after':{'present':False,'path':[1,2]}}));self.assertIsNone(x['change']['before_path']);self.assertIsNone(x['change']['after_path']);self.assertEqual(x['recorded_asns'],[])
 def test_page_dedup_and_scope(self):
  e=compact_event(self.event());p=page_impact([e,e]);self.assertEqual(p['observations'],2);self.assertEqual(p['prefixes'],1);self.assertEqual(p['viewpoints'],1);self.assertEqual(p['asns'],[1,2,3,4]);self.assertIn('Global affected',p['scope'])
 def test_aggregate_scope_is_same_prefix_path(self):
  e=self.event(proof={'facts':{'distinct_prefixes':['8.8.8.0/24','2001:db8::/32'],'withdrawn_prefix_paths':[{'prefix':'8.8.8.0/24','as_path':[1,2,3]},{'prefix':'2001:db8::/32','as_path':[1,4,5]}]}});e['prefix']=None
  x=scoped_event(e,'2001:db8::/32',3,'6');self.assertFalse(x['recorded_prefixes']);self.assertFalse(x['recorded_asns'])
  x=scoped_event(e,'8.8.8.8',3,'4');self.assertEqual(x['recorded_prefixes'],['8.8.8.0/24']);self.assertEqual(x['recorded_asns'],[1,2,3])
 def test_unknown_aggregate_mapping(self):
  e=self.event(proof={'facts':{'distinct_prefixes':['8.8.8.0/24']}});e['prefix']=None;x=scoped_event(e,'8.8.8.0/24',3);self.assertFalse(x['recorded_prefixes']);self.assertTrue(x['evidence']['facts']['prefix_asn_mapping_unknown'])
 def test_display_caps_are_lower_bounds(self):
  e=self.event(proof={'facts':{'distinct_prefixes':[f'10.0.{i}.0/24' for i in range(101)]}});x=compact_event(e);p=page_impact([x]);self.assertTrue(p['prefixes_lower_bound']);self.assertEqual(len(x['recorded_prefixes']),100);self.assertEqual(x['recorded_prefix_count'],101);self.assertIn('event_id=',x['raw_evidence_url'])
 def direction(self):
  now=datetime.now(timezone.utc);return now,{'pair':{'family':4},'source':{'as_v4':1},'target':{'ip_v4':'8.8.8.8'},'target_asn':3,'observed_at':now.isoformat()}
 def test_measurement_prefix_time_match(self):
  t,d=self.direction();m=measurement_match(d,'8.8.8.0/24',None,t-timedelta(seconds=1),t+timedelta(seconds=1));self.assertEqual(m['correlation_basis'],'same_target_prefix_and_time');self.assertIn('not ASN-wide',m['correlation_limit'])
 def test_measurement_same_asn_wrong_prefix_not_correlated(self):
  t,d=self.direction();self.assertIsNone(measurement_match(d,'1.1.1.0/24',3,t-timedelta(seconds=1),t+timedelta(seconds=1)))
 def test_wrong_family_not_correlated(self):
  t,d=self.direction();self.assertIsNone(measurement_match(d,'2001:db8::/32',None,t-timedelta(seconds=1),t+timedelta(seconds=1)))
 def test_historical_window_excludes_current_measurement(self):
  t,d=self.direction();self.assertIsNone(measurement_match(d,None,3,t-timedelta(days=13),t-timedelta(days=12)))
 def test_endpoint_asn_requires_known_metadata(self):
  t,d=self.direction();d['target_asn']=None;self.assertIsNone(measurement_match(d,None,3,t-timedelta(seconds=1),t+timedelta(seconds=1)));self.assertIsNotNone(measurement_match(d,None,1,t-timedelta(seconds=1),t+timedelta(seconds=1)))
 def test_no_timestamp_or_target_is_unknown(self):
  t,d=self.direction()
  for broken in [dict(d,observed_at=None),dict(d,target={}),dict(d,observed_at='bad')]:self.assertIsNone(measurement_match(broken,None,3,t-timedelta(seconds=1),t+timedelta(seconds=1)))
 def test_region_action_does_not_claim_health(self):
  self.assertIn('Archive-delayed',coverage_action('archive','mrt'));self.assertIn('does not restore',coverage_action('gap','ris-live'));self.assertIn('baseline',coverage_action('snapshot_required','ris-live'))

if __name__=='__main__':unittest.main()
