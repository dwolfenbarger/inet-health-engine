import unittest
from datetime import datetime,timezone,timedelta
from collectors.routing_diagnostics import apply_change,apply_withdrawal
T=datetime(2026,1,1,tzinfo=timezone.utc)
def row(t,present=True,origin=64501,path=None):
 return {'event_at':T+timedelta(seconds=t),'present':present,'origin_asn':origin if present else None,'path':(path or [64500,origin]) if present else []}
class ContinuousDiagnosticTests(unittest.TestCase):
 def test_first_observation_and_duplicates_do_not_flap(self):
  state,events,_=apply_change(None,row(0),1);self.assertEqual(events[0]['kind'],'baseline')
  for t in range(1,100):
   state,events,_=apply_change(state,row(t),1);self.assertEqual(events,[])
  self.assertEqual(state['transitions'],[])
 def test_flap_threshold_is_true_transitions_and_restart_preserves_state(self):
  import json
  state,_,_=apply_change(None,row(0),1);candidates=[]
  for t in range(1,31):
   state=json.loads(json.dumps(state))
   state,events,_=apply_change(state,row(t,t%2==0),1)
   candidates.extend(e for e in events if e['candidate'])
  self.assertEqual(len(candidates),1);self.assertEqual(candidates[0]['facts']['transitions'],25)
 def test_gaps_and_session_resets_do_not_cross_baselines(self):
  state,_,_=apply_change(None,row(0),1)
  for t in range(1,24):state,_,_=apply_change(state,row(t,t%2==0),1)
  state,events,_=apply_change(state,row(24,False,64502),2)
  self.assertEqual([e['kind'] for e in events],['baseline']);self.assertEqual(state['transitions'],[])
 def test_announced_path_oscillation_is_flapping_without_inventing_withdrawals(self):
  state,_,_=apply_change(None,row(0,path=[64500,64510,64501]),1)
  candidates=[]
  for t in range(1,31):
   state,events,_=apply_change(state,row(t,path=[64500,64510+t%2,64501]),1)
   self.assertFalse(any(e['kind']=='withdraw' for e in events))
   candidates.extend(e for e in events if e['candidate']=='bgp_flap')
  self.assertEqual(len(candidates),1)
  self.assertEqual(candidates[0]['facts']['transitions'],25)
  self.assertIn('AS-path',candidates[0]['facts']['transition_scope'])
 def test_out_of_order_changes_do_not_rewrite_state(self):
  state,_,_=apply_change(None,row(100),1)
  after,events,accepted=apply_change(state,row(10,False),1)
  self.assertEqual(after,state);self.assertFalse(accepted);self.assertEqual(events,[])
 def test_origin_candidate_requires_baseline_and_retains_real_paths(self):
  state,_,_=apply_change(None,row(0,path=[64500,64500,64501]),1)
  state,events,_=apply_change(state,row(400,origin=64502),1)
  e=events[0];self.assertEqual(e['candidate'],'bgp_hijack')
  self.assertEqual(e['before']['path'],[64500,64500,64501]);self.assertEqual(e['facts']['expected_asn'],64501)
  self.assertEqual(e['facts']['previous_announced_at'],T.timestamp());self.assertEqual(e['before']['at'],T.timestamp())
  state,events,_=apply_change(state,row(410,origin=64503),1);self.assertIsNone(events[0]['candidate'])
 def test_as_sets_remain_unknown_origins_and_paths_preserve_prepend(self):
  state,_,_=apply_change(None,row(0),1)
  state,events,_=apply_change(state,row(400,origin=None,path=[64500,[64501,64502]]),1)
  self.assertIsNone(state['origin']);self.assertFalse(any(e['candidate'] for e in events))
 def test_windows_expire_and_state_expiry_is_a_baseline(self):
  state,_,_=apply_change(None,row(0),1)
  for t in range(1,20):state,_,_=apply_change(state,row(t,t%2==0),1)
  state,events,_=apply_change(state,row(500),1);self.assertLessEqual(len(state['transitions']),1)
  _,events,_=apply_change(state,row(100000,False),1);self.assertEqual(events[0]['kind'],'baseline')
 def test_withdrawal_surge_counts_distinct_prefixes_and_bounds_memory(self):
  state=None
  for i in range(199):state,event=apply_withdrawal(state,f'192.0.{i}.0/24',100,1);self.assertIsNone(event)
  state,event=apply_withdrawal(state,'192.0.0.0/24',101,1);self.assertIsNone(event)
  state,event=apply_withdrawal(state,'192.0.199.0/24',101,1);self.assertEqual(event['withdrawal_count'],200)
  state,event=apply_withdrawal(state,'192.0.200.0/24',102,1);self.assertIsNone(event)
  state,event=apply_withdrawal(state,'192.0.201.0/24',200,1);self.assertEqual(len(state['members']),1);self.assertIsNone(event)
  state,event=apply_withdrawal(state,'192.0.202.0/24',201,2);self.assertEqual(len(state['members']),1)
 def test_capped_withdrawal_evidence_is_explicitly_a_lower_bound(self):
  prior={'epoch':1,'at':160,'members':{f'198.18.{i}.0/24':159 for i in range(256)},'reported_at':100,'capped_until':210}
  state,event=apply_withdrawal(prior,'198.18.0.0/24',161,1)
  self.assertEqual(event['withdrawal_count'],256)
  self.assertTrue(event['withdrawal_count_lower_bound']);self.assertTrue(event['prefixes_capped'])
  self.assertEqual(len(state['members']),256)
