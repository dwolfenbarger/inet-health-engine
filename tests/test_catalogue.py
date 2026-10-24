import unittest
from fastapi import HTTPException
from api.routes_catalogue import encode_cursor,decode_cursor,fingerprint,query_plan
class CatalogueContracts(unittest.TestCase):
 def test_cursor_pins_snapshot_and_scope(self):
  scope=fingerprint([13335,'transit','6','all',None,None]);token=encode_cursor(12,'ris-v6','2001:db8::/32',scope)
  self.assertEqual(decode_cursor(token,scope),[12,'ris-v6','2001:db8::/32',scope])
  with self.assertRaises(HTTPException):decode_cursor(token,fingerprint([13335,'origin','6','all',None,None]))
 def test_invalid_tokens_rejected(self):
  for token in ['!!!','e30','WzBd']:
   with self.assertRaises(HTTPException):decode_cursor(token,'scope')
 def test_role_search_uses_membership_not_origin_only(self):
  conditions,args=query_plan(7,4200000000,'transit','6','all',None,None)
  self.assertIn('r.transit @> ARRAY[$2::bigint]',conditions);self.assertEqual(args,[7,4200000000,6])
 def test_ip_and_prefix_canonicalization(self):
  c,a=query_plan(1,None,'any','all','rrc11','1.1.1.1/24',None)
  self.assertEqual(a,[1,'rrc11','1.1.1.0/24'])
  with self.assertRaises(ValueError):query_plan(1,None,'any','all','all',None,'300.1.1.1')
if __name__=='__main__':unittest.main()
