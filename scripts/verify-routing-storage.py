"""Real DB regressions for atomicity, session invalidation and replay ordering.

All records are rolled back; works against either native PostgreSQL or Timescale.
"""
import asyncio,os,sys,uuid
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from contextlib import asynccontextmanager
root=Path('/home/david/inet-health-engine')
for line in (root/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
candidate=Path(sys.argv[1]) if len(sys.argv)>1 else root
sys.path[:0]=[str(candidate/'collectors'),str(candidate/'api')]
async def main():
 import asyncpg
 from collectors.routing_store import register,commit_messages,open_gap,archive_batch
 from collectors.routing_model import mrt_change
 c=await asyncpg.connect(os.environ['TIMESCALE_DSN'])
 class Pool:
  @asynccontextmanager
  async def acquire(self):yield c
  async def fetch(self,*a):return await c.fetch(*a)
  async def fetchval(self,*a):return await c.fetchval(*a)
 async def pool():return Pool()
 tx=c.transaction();await tx.start()
 try:
  with patch('collectors.routing_store.get_pg_pool',pool):
   obs={'id':'regression-routing','source':'ris-continuous','collector':'rrc23','peer_ip':'2001:db8::1','peer_asn':4288000245,'mode':'ris-live'}
   now=datetime.now(timezone.utc)
   def frame(ident,seconds=0,kind='UPDATE',**extra):
    data={'host':'rrc23','peer':obs['peer_ip'],'peer_asn':obs['peer_asn'],'type':kind,'id':ident,
          'timestamp':(now+timedelta(seconds=seconds)).timestamp(),'path':[4288000245,13335],
          'announcements':[{'prefixes':['1.1.1.0/24']}]}
    return ({'type':'ris_message','data':{**data,**extra}},now)
   await register(obs);await open_gap(obs['id'],'test-start')
   first=frame('A');await commit_messages(obs,[first]);await commit_messages(obs,[first])
   row=await c.fetchrow('SELECT * FROM routing_observers WHERE id=$1',obs['id'])
   assert row['messages']==1 and row['sequence']==1
   assert await c.fetchval('SELECT count(*) FROM routing_routes WHERE observer=$1',obs['id'])==1
   # A malformed second frame rolls back the whole batch, including its journal/checkpoint.
   bad=frame('bad',peer='2001:db8::9')
   try:await commit_messages(obs,[frame('A2',1),bad]);raise AssertionError('Identity mismatch accepted')
   except ValueError:pass
   assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM routing_messages WHERE observer=$1 AND message_id='A2')",obs['id'])
   await commit_messages(obs,[frame('W',2,announcements=[],withdrawals=['1.1.1.0/24'])])
   observed=dict(await c.fetchrow('SELECT * FROM routing_observers WHERE id=$1',obs['id']))
   old={'timestamp':(now+timedelta(seconds=1)).timestamp(),'elem_type':'A','peer_ip':obs['peer_ip'],
        'peer_asn':obs['peer_asn'],'prefix':'1.1.1.0/24','as_path':'4288000245 13335'}
   await archive_batch(obs,observed['epoch'],observed['generation'],[(1,mrt_change(old,obs))],1,'archive')
   assert not await c.fetchval('SELECT present FROM routing_routes WHERE observer=$1',obs['id'])
   # Reconnection starts at the committed event, not the receive wall clock.
   await open_gap(obs['id'],'test-reconnect')
   await commit_messages(obs,[frame('old-replay',-5)])
   assert await c.fetchval('SELECT count(*) FROM routing_gaps WHERE observer=$1 AND until_at IS NULL',obs['id'])==1
   await commit_messages(obs,[frame('KA',3,kind='KEEPALIVE')])
   assert await c.fetchval('SELECT count(*) FROM routing_gaps WHERE observer=$1 AND until_at IS NULL',obs['id'])==0
   # An IP lookup must fall back to a present covering route after a more-specific withdrawal.
   await commit_messages(obs,[frame('cover',3,announcements=[{'prefixes':['1.0.0.0/8']}])])
   from fastapi import FastAPI
   import httpx
   from api.routes_routing import router
   app=FastAPI();app.include_router(router)
   with patch('api.routes_routing.get_pg_pool',pool):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
     response=await client.get('/routing/routes?observer=regression-routing&ip=1.1.1.1')
     assert response.status_code==200,response.text
     body=response.json();assert body['health_score'] is None
     assert body['routes'][0]['prefix']=='1.0.0.0/8' and body['routes'][0]['peer_asn']==4288000245
     response=await client.get('/routing/routes?observer=regression-routing&prefix=1.1.1.0/24&include_withdrawn=true')
     assert response.status_code==200 and not response.json()['routes'][0]['present']
     assert (await client.get('/routing/routes?ip=1.1.1.1&include_withdrawn=true')).status_code==422
     assert (await client.get('/routing/routes?ip=invalid')).status_code==422
     assert (await client.get('/routing/routes?limit=201')).status_code==422
   await c.execute("UPDATE routing_observers SET baseline_at=$2,baseline_state='reconstructed',snapshot_v4=1 WHERE id=$1",obs['id'],now)
   await commit_messages(obs,[frame('DOWN',4,kind='RIS_PEER_STATE',state='down')])
   reset=await c.fetchrow('SELECT * FROM routing_observers WHERE id=$1',obs['id'])
   assert reset['epoch']==observed['epoch']+1 and reset['baseline_at'] is None and reset['snapshot_v4']==0
   assert not await c.fetchval('''SELECT EXISTS(SELECT 1 FROM routing_routes r JOIN routing_observers o ON o.id=r.observer
       WHERE o.id=$1 AND r.epoch=o.epoch AND r.generation=o.generation)''',obs['id'])
   try:
    await archive_batch(obs,observed['epoch'],observed['generation'],[(1,mrt_change(old,obs))],1,'obsolete')
    raise AssertionError('Obsolete archive epoch accepted')
   except RuntimeError:pass
   other={**obs,'id':'regression-routing-other','peer_ip':'2001:db8::2'}
   await register(other)
   other_frame=frame('other');other_frame[0]['data']['peer']=other['peer_ip']
   await commit_messages(other,[other_frame])
   assert await c.fetchval('SELECT present FROM routing_routes WHERE observer=$1',other['id'])
  print('ROUTING_DB_REGRESSIONS_PASSED: atomic batch, dedup, replay ordering, gap boundaries, session epochs, peer separation',flush=True)
 finally:
  await tx.rollback()
  assert await c.fetchval("SELECT count(*) FROM routing_observers WHERE id LIKE 'regression-routing%'")==0
  await c.close()
 print('All routing regression records rolled back',flush=True)
asyncio.run(main())
