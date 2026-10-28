"""Fourteen-day history/API/cursor/state proof on synthetic isolated DEV only."""
import asyncio,json,os,socket,sys,uuid
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import AsyncMock,patch
from urllib.parse import urlsplit
assert socket.gethostname()=='inet-health-dev1'
R=Path(__file__).resolve().parents[1];sys.path[:0]=[str(R/'collectors'),str(R/'api')]
import asyncpg,httpx
from fastapi import FastAPI
import api.routes_diagnostics as api
from collectors.routing_diagnostic_worker import process_frames,maintenance
UTC=timezone.utc
async def main():
 db=os.environ['INET_DIAGNOSTICS_QA_DATABASE'];assert db.startswith('inet_ret14_qa_') and urlsplit(os.environ['TIMESCALE_DSN']).path=='/'+db
 p=await asyncpg.create_pool(os.environ['TIMESCALE_DSN'],min_size=1,max_size=4)
 checks=[];now=datetime.now(UTC);base=now-timedelta(days=13);o={'id':'qa-ret14','collector':'rrc00','peer_ip':'192.0.2.14','peer_asn':64550};pending=None
 def frame(seq,t,path):
  at=base+timedelta(seconds=t);raw={'data':{'host':'rrc00.ripe.net','peer':o['peer_ip'],'peer_asn':o['peer_asn'],'type':'UPDATE','timestamp':at.timestamp(),'id':'ret14-'+str(seq),'path':path,'announcements':[{'prefixes':['198.18.240.0/24']}],'withdrawals':[]}}
  return {'message_id':'ret14-'+str(seq),'sequence':seq,'event_at':at,'received_at':now,'payload':raw}
 async def apply(frames):
  async with p.acquire() as c,c.transaction():
   cp=await c.fetchrow('SELECT * FROM routing_diagnostic_cursors WHERE observer=$1 FOR UPDATE',o['id']);return await process_frames(c,o,cp,frames,[])
 class Cache:
  def __init__(self):self.values={};self.ttls=[]
  async def get(self,k):return self.values.get(k)
  async def setex(self,k,ttl,v):self.values[k]=v;self.ttls.append(ttl)
 try:
  assert await p.fetchval("SELECT relkind::text FROM pg_class WHERE oid='routing_diagnostic_events'::regclass")=='p'
  await p.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation) VALUES($1,'fixture','rrc00',$2::text::inet,$3,'ris-live','{}',$4)",o['id'],o['peer_ip'],o['peer_asn'],uuid.uuid4())
  await p.execute('INSERT INTO routing_diagnostic_cursors(observer,sequence,received_at,event_at,gap_seen,started_at) VALUES($1,0,$2,$2,$2,$2)',o['id'],base)
  await apply([frame(1,0,[64550,64560,64551]),frame(2,10,[64550,64561,64551]),frame(3,400,[64550,64562,64552])])
  before=await p.fetchval('SELECT count(*) FROM routing_diagnostic_events WHERE observer=$1',o['id']);assert before==3
  await apply([frame(3,400,[64550,64562,64552])]);assert await p.fetchval('SELECT count(*) FROM routing_diagnostic_events WHERE observer=$1',o['id'])==before
  await p.execute("INSERT INTO routing_gaps(id,observer,opened_at,from_at,until_at,reason,state) VALUES($1,$2,$3,$3,$4,'FixtureSourceGap','waiting_archive')",uuid.uuid4(),o['id'],base+timedelta(seconds=50),base+timedelta(seconds=90))
  # Keep a later change uncommitted while the investigation fences committed heads.
  pending=await p.acquire();tx=pending.transaction();await tx.start();cp=await pending.fetchrow('SELECT * FROM routing_diagnostic_cursors WHERE observer=$1',o['id']);await process_frames(pending,o,cp,[frame(4,600,[64550,64563,64553])],[])
  cache=Cache();app=FastAPI();app.include_router(api.router)
  with patch.object(api,'get_pg_pool',AsyncMock(return_value=p)),patch.object(api,'get_redis',AsyncMock(return_value=cache)):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://qa') as c:
    async def get(path,params,expected=200):
     r=await c.get('/diagnostics'+path,params=params);assert r.status_code==expected,(path,r.status_code,r.text[:300]);return r.json()
    end=(base+timedelta(seconds=1000)).isoformat();q={'asn':64550,'minutes':60,'until':end,'limit':1}
    first=await get('/history',q);assert first['items'] and first['next_cursor'];token=first['next_cursor'];assert first['history_gaps'][0]['reason']=='FixtureSourceGap'
    assert first['requested_window']['through']==end and not first['retention_clipped']
    await tx.commit();await p.release(pending);pending=None
    page=first;seen=[]
    while True:
     seen.extend(x['event_id'] for x in page['items']);assert all(x['sequence']<=3 for x in page['items'])
     assert page['as_of']==first['as_of'] and page['requested_window']==first['requested_window']
     if not page['next_cursor']:break
     page=await get('/history',q|{'cursor':page['next_cursor']})
    assert len(seen)==3 and len(set(seen))==3 and all(0<t<=300 for t in cache.ttls)
    await get('/history',q|{'cursor':token,'until':(base+timedelta(seconds=1100)).isoformat()},422)
    rows=await get('/history',{'asn':64551,'minutes':20160});assert len(rows['items'])==3
    event=next(x for x in rows['items'] if x['candidate']=='bgp_hijack');d=await get('/detail',{'event_id':event['event_id']})
    assert d['candidate']['raw_data']['before']['path']==[64550,64561,64551]
    assert d['candidate']['raw_data']['after']['path']==[64550,64562,64552]
    assert d['previous_origin_announcement']['as_path']==[64550,64561,64551]
    assert d['candidate']['time']==event['event_at']
    for resource in ['198.18.240.0/24','198.18.240.1']:
     h=await get('/history',{'asn':64550,'resource':resource,'minutes':20160,'until':end});assert len(h['items'])==4
    await get('/history',{'asn':64550,'minutes':20161},422)
    await get('/history',{'asn':64550,'until':(now+timedelta(days=1)).isoformat()},422)
    await get('/history',{'asn':64550,'until':base.replace(tzinfo=None).isoformat()},422)
    outside=await get('/history',{'asn':64550,'until':(now-timedelta(days=15)).isoformat(),'minutes':60});assert outside['outside_retention'] and outside['effective_window'] is None and not outside['items']
    clipped=await get('/history',{'asn':64550,'until':(now-timedelta(days=1)).isoformat(),'minutes':20160});assert clipped['retention_clipped'] and clipped['items']
    snap=json.loads(cache.values['noc:history:'+token]);snap['expires_at']=(now-timedelta(seconds=1)).isoformat();cache.values['noc:history:'+token]=json.dumps(snap)
    await get('/history',q|{'cursor':token},409)
  checks+=['13-day-old baseline/path/origin evidence is retrievable with exact before/after paths and event times','Historical end-time, prefix/IP/ASN filtering and source gaps are scoped correctly','Committed-head pagination survives a delayed transaction, rejects filter changes and expiry, and never extends its TTL','Future/naive/over-limit windows are refused; out-of-retention windows stay explicitly empty']
  # App worker accounts for actual child storage, not the zero-size parent.
  assert await maintenance(p)
  measured=await p.fetchval('SELECT measured_bytes FROM routing_diagnostic_policy WHERE id')
  physical=await p.fetchval("SELECT sum(pg_total_relation_size(i.inhrelid)) FROM pg_inherits i WHERE i.inhparent='routing_diagnostic_events'::regclass")
  assert measured>=physical and physical>0
  checks.append('Partition children/index/TOAST allocation is included in the capacity guard')
  print(json.dumps({'pass':True,'checks':checks,'historical_rows_checked':len(seen),'measured_bytes':int(measured),'history_child_bytes':int(physical)},indent=2))
 finally:
  if pending:
   await tx.rollback();await p.release(pending)
  await p.close()
asyncio.run(main())
