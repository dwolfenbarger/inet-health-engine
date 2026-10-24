import unittest,httpx
from datetime import datetime,timedelta,timezone
from api.local_validation import validate
class LocalValidation(unittest.IsolatedAsyncioTestCase):
 async def check_response(self,body):
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=body)),base_url='http://validator') as client:
   return await validate(client,'2001:db8::/32',4288000245,self.now)
 def setUp(self):
  self.now=datetime.now(timezone.utc);self.body={'generatedTime':self.now.isoformat(),
   'validated_route':{'route':{'prefix':'2001:db8::/32','origin_asn':'AS4288000245'},'validity':{'state':'valid','VRPs':{'matched':[]}}}}
 async def test_focused_validation_preserves_provider_proof(self):
  proof=await self.check_response(self.body);self.assertEqual(proof['origin_asn'],4288000245);self.assertEqual(proof['status'],'valid');self.assertEqual(proof['validity'],self.body['validated_route']['validity'])
 async def test_stale_is_unknown(self):
  self.body['generatedTime']=(self.now-timedelta(minutes=61)).isoformat();self.assertIsNone(await self.check_response(self.body))
 async def test_different_origin_is_unknown(self):
  self.body['validated_route']['route']['origin_asn']='AS13335';self.assertIsNone(await self.check_response(self.body))
 async def test_malformed_is_unknown(self):self.assertIsNone(await self.check_response({}))
