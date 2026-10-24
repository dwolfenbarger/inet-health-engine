import unittest
from unittest.mock import AsyncMock,patch
from datetime import datetime,timezone,timedelta
from fastapi import HTTPException
from api.routes_catalogue import encode_cursor,decode_cursor,fingerprint,query_plan,membership_plan
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
 def test_sorted_membership_scope_preserves_role_family_and_view(self):
  g={'id':2,'observers':[{'id':'v6','collector':'rrc21'},{'id':'v4','collector':'rrc11'}]}
  keys,args=membership_plan(g,15547,'transit','6','rrc21','2001:db8::1/32')
  self.assertEqual(args,[2,15547,6,['v6'],'2001:db8::/32'])
  self.assertIn('(roles & 2)<>0',keys)
  self.assertIn('observer=ANY($4::text[])',keys)
 def test_unobserved_collector_does_not_expand_scope(self):
  _,args=membership_plan({'id':2,'observers':[]},13335,'any','all','missing',None)
  self.assertEqual(args,[2,13335,[]])
 def test_ip_and_prefix_canonicalization(self):
  c,a=query_plan(1,None,'any','all','rrc11','1.1.1.1/24',None)
  self.assertEqual(a,[1,'rrc11','1.1.1.0/24'])
  with self.assertRaises(ValueError):query_plan(1,None,'any','all','all',None,'300.1.1.1')
class CatalogueFailureSemantics(unittest.IsolatedAsyncioTestCase):
 async def test_missing_metadata_does_not_hide_observed_asn(self):
  from api.routes_catalogue import detail
  g={'id':2,'captured_at':datetime.now(timezone.utc)-timedelta(hours=2),'summary':{},'observers':[]}
  pool=AsyncMock();pool.fetchrow.side_effect=[g,{'asn':13335,'routes':13343}];pool.fetch.return_value=[];pool.fetchval.return_value=True
  with patch('api.routes_catalogue.get_pg_pool',AsyncMock(return_value=pool)),patch('api.routes_catalogue.get_redis',AsyncMock(side_effect=RuntimeError('metadata unavailable'))):
   result=await detail(13335,generation_id=2)
  self.assertTrue(result['observed']);self.assertEqual(result['inventory']['routes'],13343)
  self.assertEqual(result['metadata']['state'],'unavailable');self.assertIsNone(result['metadata']['holder'])
  self.assertTrue(result['generation']['stale'])
 async def test_retired_generation_never_becomes_empty_success(self):
  from api.routes_catalogue import ensure_retained
  pool=AsyncMock();pool.fetchval.return_value=False
  with self.assertRaises(HTTPException) as e:await ensure_retained(pool,2)
  self.assertEqual(e.exception.status_code,409)
 async def test_unpublished_inventory_is_unavailable(self):
  from api.routes_catalogue import generation
  pool=AsyncMock();pool.fetchrow.return_value=None
  with self.assertRaises(HTTPException) as e:await generation(pool)
  self.assertEqual(e.exception.status_code,503)
if __name__=='__main__':unittest.main()
