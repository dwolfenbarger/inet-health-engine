import json,tempfile,unittest
from pathlib import Path
from collectors.archive_profile import Bundle,verify
class OperationalBundle(unittest.TestCase):
 def test_operational_manifest_requires_receipt_instead_of_pilot_expiry_flag(self):
  with tempfile.TemporaryDirectory() as temporary:
   bundle=Bundle(Path(temporary)/'segment',purpose='journal');bundle.add('routing_messages',{'message_id':'x','path':[4288000245]})
   manifest=bundle.finish({'segment':'routing_messages_p2026102400'})
   self.assertEqual(manifest['schema'],'inet-journal-segment-v1');self.assertNotIn('expiry_eligible',manifest)
   self.assertEqual(manifest['local_retirement_policy'],'verified-receipt-and-24h');self.assertEqual(verify(bundle.root)['records'],1)
