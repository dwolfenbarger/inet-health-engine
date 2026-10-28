"""Actual historical HTTP page around partition expiry, using a DEV-only clock."""
import asyncio,json,os,socket,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import AsyncMock,patch
from urllib.parse import urlsplit
assert socket.gethostname()=='inet-health-dev1'
R=Path(__file__).resolve().parents[1];sys.path[:0]=[str(R/'api')]
import asyncpg,httpx
from fastapi import FastAPI
import api.routes_diagnostics as api
UTC=timezone.utc
async def main():
 assert urlsplit(os.environ['TIMESCALE_DSN']).path=='/inet_ret14_qa_20261028'
 values=json.loads(Path('/opt/inet-ret14-20261028/grace-times.json').read_text());at=datetime.fromisoformat(values['now']);captured=datetime.fromisoformat(values['captured'])
 class Clock(datetime):
  @classmethod
  def now(cls,tz=None):return at if tz else at.replace(tzinfo=None)
 class Cache:
  def __init__(self,v):self.v=v
  async def get(self,k):return self.v if k=='noc:history:00000000-0000-4000-8000-000000000014' else None
  async def setex(self,*a):pass
 pool=await asyncpg.create_pool(os.environ['TIMESCALE_DSN'],min_size=1,max_size=2)
 try:
  heads={r['observer']:r['sequence'] for r in await pool.fetch('SELECT observer,sequence FROM routing_diagnostic_cursors')}
  scope={'asn':64550,'resource':None,'minutes':20160,'family':'all','collector':'all','until':None}
  cache=Cache(json.dumps({'scope':scope,'as_of':captured.isoformat(),'expires_at':(captured+timedelta(minutes=5)).isoformat(),'retention_hours':336,'heads':heads}))
  app=FastAPI();app.include_router(api.router)
  with patch.object(api,'get_pg_pool',AsyncMock(return_value=pool)),patch.object(api,'get_redis',AsyncMock(return_value=cache)),patch.object(api,'datetime',Clock):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://qa') as c:
    r=await c.get('/diagnostics/history',params={'asn':64550,'minutes':20160,'cursor':'00000000-0000-4000-8000-000000000014'})
    expected=values['expected'];assert r.status_code==expected,r.text
    if expected==200:assert len(r.json()['items'])==50 and all(x['kind']=='retention_grace_fixture' for x in r.json()['items'])
  print(json.dumps({'pass':True,'expected_http':expected,'at':values['now'],'captured':values['captured']}))
 finally:await pool.close()
asyncio.run(main())
