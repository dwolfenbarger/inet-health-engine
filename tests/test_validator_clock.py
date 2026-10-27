import unittest
from datetime import datetime,timedelta,timezone
from collectors.rpki_collector import parse_validations
class ValidatorClock(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,10,24,tzinfo=timezone.utc);self.pair=('2001:db8::/32',4288000245)
  self.data={'generatedTime':(self.now-timedelta(minutes=25)).isoformat(),'validated_routes':[
    {'route':{'prefix':self.pair[0],'origin_asn':str(self.pair[1])},'validity':{'state':'valid','VRPs':{'matched':[]}}}]}
 def test_long_completed_validation_is_usable(self):
  completed=self.now-timedelta(minutes=1)
  r=parse_validations(self.data,[self.pair],self.now,completed=completed,serial=67)[0]
  self.assertEqual(r['observed_at'],completed);self.assertEqual(r['payload']['generated_at'],self.data['generatedTime']);self.assertEqual(r['payload']['snapshot_serial'],67)
 def test_old_completion_is_rejected(self):
  with self.assertRaises(ValueError):parse_validations(self.data,[self.pair],self.now,completed=self.now-timedelta(minutes=61),max_age=timedelta(minutes=60))
 def test_old_generation_without_completion_is_rejected(self):
  with self.assertRaises(ValueError):parse_validations(self.data,[self.pair],self.now)
 def test_future_completion_is_rejected(self):
  with self.assertRaises(ValueError):parse_validations(self.data,[self.pair],self.now,completed=self.now+timedelta(minutes=3))
 def test_incoherent_generation_is_rejected(self):
  self.data['generatedTime']=(self.now-timedelta(hours=3)).isoformat()
  with self.assertRaises(ValueError):parse_validations(self.data,[self.pair],self.now,completed=self.now)
 def test_missing_pair_is_never_not_found(self):
  self.data['validated_routes']=[]
  with self.assertRaises(ValueError):parse_validations(self.data,[self.pair],self.now,completed=self.now)
