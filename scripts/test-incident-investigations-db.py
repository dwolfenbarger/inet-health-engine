"""Actual PostgreSQL and ASGI qualification. Synthetic dedicated DEV database only."""
import asyncio,json,os,socket,sys,uuid
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import AsyncMock,patch
from urllib.parse import urlsplit
assert socket.gethostname()=='inet-health-dev1'
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'api'),str(ROOT/'collectors')]
import asyncpg,httpx
from fastapi import FastAPI
from api import routes_paths as paths,routes_measurements as measured,routes_incidents as incidents,routes_visibility as visibility,routes_diagnostics as diagnostics
from api.catalogue_backend import OFFSET
from collectors import catalogue_incremental as inc
UTC=timezone.utc;DB=os.environ['INET_PATHS_QA_DATABASE'];assert DB.startswith('inet_paths_qa_')


class Cache:
 def __init__(self):self.values={};self.ttls=[]
 async def get(self,k):return self.values.get(k)
 async def setex(self,k,ttl,v):self.values[k]=v;self.ttls.append(ttl)


async def main():
 assert urlsplit(os.environ['TIMESCALE_DSN']).path=='/'+DB
 pool=await asyncpg.create_pool(os.environ['TIMESCALE_DSN'],min_size=1,max_size=5)
 checks=[];cache=Cache();now=datetime.now(UTC);base=now-timedelta(days=13);pending=None
 try:
  assert await pool.fetchval('SELECT current_database()')==DB
  for name in ['20261023-routing-state.sql','20261026-routing-diagnostics.sql','20261028-diagnostic-retention-14d.sql','20261024-asn-catalogue.sql']:
   await pool.execute((ROOT/'docker/migrations'/name).read_text())
  await pool.execute('UPDATE routing_diagnostic_policy SET enabled=true,state=\'running\',retention_hours=336,checked_at=now() WHERE id')
  for oid,collector,peer in [('qa-a','rrc11',64550),('qa-b','rrc23',64560)]:
   await pool.execute("INSERT INTO routing_observers(id,source,collector,peer_ip,peer_asn,mode,metadata,generation,stream_state,peer_state,baseline_state,event_at,received_at,committed_at,sequence) VALUES($1,'ris-continuous',$2,$3::text::inet,$4,'ris-live','{}',$5,'connected','up','reconstructed',$6,$6,$6,500)",oid,collector,'192.0.2.'+str(peer-64549),peer,uuid.uuid4(),now)
   await pool.execute("INSERT INTO routing_diagnostic_cursors(observer,sequence,received_at,event_at,gap_seen,heartbeat_at,status) VALUES($1,500,$2,$2,$2,$2,'waiting')",oid,now)
  await pool.execute('CREATE TABLE inet_catalog.routes_501 PARTITION OF inet_catalog.routes FOR VALUES IN (501)')
  await pool.execute(inc.DDL);await pool.execute(inc.VERSION_DDL)
  await pool.execute('''CREATE TABLE inet_incremental.resets(observer text PRIMARY KEY); CREATE TABLE inet_incremental.dirty(observer text,prefix cidr,queued_at timestamptz DEFAULT now());''')
  await pool.execute(inc.RUNTIME_DDL)
  await pool.execute("ALTER TABLE inet_incremental.publications ADD COLUMN summary jsonb NOT NULL DEFAULT '{}',ADD COLUMN observers jsonb NOT NULL DEFAULT '[]'")
  for table in ['current_routes','current_memberships','asns']:
   await pool.execute(f'CREATE TRIGGER incremental_version BEFORE INSERT OR UPDATE OR DELETE ON inet_incremental.{table} FOR EACH ROW EXECUTE FUNCTION inet_incremental.version_change()')
  await pool.execute('UPDATE inet_incremental.runtime SET initialized=true,reset_barrier=false')
  observers=[{'id':'qa-a','collector':'rrc11','peer_ip':'192.0.2.1'},{'id':'qa-b','collector':'rrc23','peer_ip':'192.0.2.11'}]
  summary={'routes':8,'prefixes':6,'origins':4,'observers':2,'asns':8,'facets':[]}
  await pool.execute('INSERT INTO inet_catalog.generations(id,published_at,observers,summary) OVERRIDING SYSTEM VALUE VALUES(501,now(),$1::jsonb,$2::jsonb)',json.dumps(observers),json.dumps(summary))
  fixtures=[('qa-a','198.18.0.0/24',[64550,64510,64520,64530]),('qa-b','198.18.0.0/24',[64560,64510,64521,64530]),
            ('qa-a','198.18.0.0/25',[64550,64599]),('qa-a','2001:db8::/32',[64550,64510,[64522,64523],64530]),
            ('qa-a','2001:db8:1::/48',[64550,64510,4294967295]),('qa-a','198.18.1.0/24',[64550,64510,64510,64530]),
            ('qa-b','198.18.2.0/24',[64560,64510,64520,64510,64530]),('qa-a','8.8.8.0/24',[64550,64510,64530])]
  async with pool.acquire() as c,c.transaction():
   version=await inc.begin_version(c);assert version==1
   for observer,prefix,path in fixtures:
    member=sorted({a for a in path if type(a) is int});origin=path[-1];peer=64550 if observer=='qa-a' else 64560;collector='rrc11' if observer=='qa-a' else 'rrc23';af=6 if ':' in prefix else 4
    args=[observer,prefix,origin,peer,collector,[a for a in member if a!=origin],member,af,now,json.dumps(path),'{}','fixture-'+prefix,2]
    await c.execute('INSERT INTO inet_catalog.routes VALUES(501,$1,$2::text::cidr,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12,$13)',*args)
    await c.execute('INSERT INTO inet_incremental.current_routes('+inc.FIELDS+') VALUES($1,$2::text::cidr,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12,$13)',*args)
    for a in member:
     await c.execute('INSERT INTO inet_incremental.current_memberships(asn,observer,prefix,roles) VALUES($1,$2,$3::text::cidr,1)',a,observer,prefix)
    await c.execute('INSERT INTO routing_routes(observer,prefix,epoch,generation,event_at,rank,event_order,present,origin_asn,path,attrs,evidence_id) SELECT $1,$2::text::cidr,epoch,generation,$3,2,\'fixture\',true,$4,$5::jsonb,\'{}\',\'fixture\' FROM routing_observers WHERE id=$1',observer,prefix,now,origin,json.dumps(path))
   await c.execute('UPDATE inet_incremental.publications SET ready=true,observers=$1::jsonb,summary=$2::jsonb WHERE id=1',json.dumps(observers),json.dumps(summary))
  app=FastAPI();app.include_router(paths.router);app.include_router(measured.router)
  with patch.object(paths,'get_pg_pool',AsyncMock(return_value=pool)),patch.object(paths,'get_redis',AsyncMock(return_value=cache)):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://qa') as client:
    async def get(path,params,code=200):
     r=await client.get('/paths/'+path,params=params);assert r.status_code==code,(path,r.status_code,r.text[:500]);return r.json()
    q={'source_asn':64510,'destination_asn':64530,'limit':1}
    for backend in ['0','1']:
     os.environ['INET_INCREMENTAL_CATALOGUE']=backend
     page=await get('routes',q);first=page;seen=[]
     while True:
      seen.extend((r['observer'],r['prefix']) for r in page['items'])
      assert page['generation']['id']==first['generation']['id']
      if not page['next_cursor']:break
      page=await get('routes',q|{'cursor':page['next_cursor']})
     assert len(seen)==6 and len(set(seen))==6,seen
     await get('routes',q|{'cursor':first['next_cursor'],'source_asn':64520},422)
     v=await get('routes',q|{'resource':'198.18.0.1','limit':25});a=next(r for r in v['items'] if r['observer']=='qa-a')
     assert a['prefix']=='198.18.0.0/25' and a['segment']['state']=='origin_mismatch'
     b=next(r for r in v['items'] if r['observer']=='qa-b');assert b['prefix']=='198.18.0.0/24' and b['segment']['ordered']
     v=await get('routes',q|{'resource':'198.18.0.0/24','limit':25});assert len(v['items'])==2
     v=await get('routes',q|{'resource':'2001:db8::/32','family':'6'});assert v['items'][0]['segment']['state']=='ambiguous_segment'
     v=await get('routes',q|{'destination_asn':4294967295});assert v['items'][0]['segment']['ordered']
     await get('routes',q|{'resource':'198.18.0.1','family':'6'},422);await get('routes',q|{'collector':'invented'},422)
    checks.append('Legacy and incremental current routes: pinned pages, exact-prefix and LPM-before-ASN matching, divergent viewpoints, AS_SET, prepends, four-byte ASN and invalid scope')
    os.environ['INET_INCREMENTAL_CATALOGUE']='1';first=await get('routes',q)
    async with pool.acquire() as c,c.transaction():
     v=await inc.begin_version(c);assert v==2
     await c.execute("UPDATE inet_incremental.current_routes SET path='[64550,64510,64525,64530]' WHERE observer='qa-a' AND prefix='198.18.0.0/24'")
     await c.execute('UPDATE inet_incremental.publications SET ready=true,observers=$1::jsonb,summary=$2::jsonb WHERE id=2',json.dumps(observers),json.dumps(summary))
    old=await get('routes',q|{'resource':'198.18.0.0/24','generation_id':OFFSET+1,'limit':25})
    new=await get('routes',q|{'resource':'198.18.0.0/24','limit':25})
    assert old['items'][0]['segment']['raw_path'][2]==64520 and new['items'][0]['segment']['raw_path'][2]==64525
    await pool.execute('DELETE FROM inet_incremental.publications WHERE id=1')
    await get('routes',q|{'cursor':first['next_cursor']},409)
    checks.append('A live route update does not leak into an older incremental browsing generation; expired generation returns409')
    async def event(sequence,at,prefix,evidence,candidate=None,asns=None):
     ident=uuid.uuid4();await pool.execute('''INSERT INTO routing_diagnostic_events(event_id,observer,sequence,ordinal,epoch,event_at,received_at,prefix,kind,candidate,origin_asn,expected_asn,involved_asns,evidence)
       VALUES($1,'qa-a',$2,0,1,$3,$4,$5::text::cidr,'path_change',$6,64530,64530,$7,$8::jsonb)''',ident,sequence,at,now,prefix,candidate,asns or [64510,64530],json.dumps(evidence));return str(ident)
    for i in range(270):await event(i+1,now-timedelta(seconds=i+10),'198.18.2.0/24',{'before':{'present':True,'path':[64510,64599]},'after':{'present':True,'path':[64520,64530]}})
    ids=[]
    for i in range(5):ids.append(await event(280+i,base+timedelta(seconds=i),'198.18.10.0/24',{'before':{'present':True,'path':[64550,64510,64520,64530]},'after':{'present':True,'path':[64550,64510,64531]}},'bgp_hijack'))
    await event(290,base+timedelta(seconds=20),None,{'facts':{'distinct_prefixes':['198.18.10.0/24','2001:db8:20::/48'],'withdrawn_prefix_paths':[{'prefix':'198.18.10.0/24','as_path':[64550,64510,64530]},{'prefix':'2001:db8:20::/48','as_path':[64550,64510,64531]}]}},'withdrawal_surge',[64510,64530,64531])
    await pool.execute("INSERT INTO routing_gaps(id,observer,opened_at,from_at,until_at,reason,state) VALUES($1,'qa-a',$2,$2,$3,'FixtureGap','waiting_archive')",uuid.uuid4(),base+timedelta(seconds=1),base+timedelta(seconds=4))
    pending=await pool.acquire();tx=pending.transaction();await tx.start();delayed=uuid.uuid4()
    await pending.execute("INSERT INTO routing_diagnostic_events(event_id,observer,sequence,ordinal,epoch,event_at,received_at,prefix,kind,involved_asns,evidence) VALUES($1,'qa-a',501,0,1,$2,$2,'198.18.10.0/24','path_change',ARRAY[64510,64530]::bigint[],'{\"before\":{\"present\":true,\"path\":[64510,64530]}}')",delayed,now-timedelta(seconds=1))
    await pending.execute("UPDATE routing_diagnostic_cursors SET sequence=501 WHERE observer='qa-a'")
    hq=q|{'minutes':20160};first=await get('history',hq);assert not first['items'] and first['next_cursor'] and first['scanned_rows']==250
    await tx.commit();await pool.release(pending);pending=None
    page=first;seen=[]
    while True:
     seen.extend(x['event_id'] for x in page['items']);assert page['as_of']==first['as_of'] and page['expires_at']==first['expires_at']
     if not page['next_cursor']:break
     page=await get('history',hq|{'cursor':page['next_cursor']})
    assert len(seen)==6 and len(set(seen))==6 and str(delayed) not in seen,(len(seen),seen)
    assert all(0<t<=300 for t in cache.ttls)
    await get('history',hq|{'cursor':first['next_cursor'],'minutes':1440},422)
    h=await get('history',hq|{'resource':'2001:db8:20::/48','family':'6'});assert not h['items']
    h=await get('history',hq|{'resource':'198.18.10.1','limit':25});all_items=list(h['items']);assert h['history_gaps']
    while h['next_cursor']:
     h=await get('history',hq|{'resource':'198.18.10.1','limit':25,'cursor':h['next_cursor']});all_items+=h['items']
    assert len(all_items)==7;h['items']=all_items
    assert next(i for i in h['items'] if i['candidate']=='bgp_hijack')['pair_matches'][0]['side']=='before'
    snap=json.loads(cache.values['noc:paths-history:'+first['next_cursor']]);snap['expires_at']=(now-timedelta(seconds=1)).isoformat();cache.values['noc:paths-history:'+first['next_cursor']]=json.dumps(snap)
    await get('history',hq|{'cursor':first['next_cursor']},409)
    outside=await get('history',hq|{'until':(now-timedelta(days=15)).isoformat()});assert outside['outside_retention'] and not outside['items'] and outside['effective_window'] is None
    for extra in [{'minutes':20161},{'until':(now+timedelta(days=1)).isoformat()},{'until':now.replace(tzinfo=None).isoformat()}]:await get('history',hq|extra,422)
    checks.append('13-day victim/path evidence, bounded empty-page continuation, delayed-commit head fences, no duplicate/lost records, absolute expiry, historical gaps and same-prefix withdrawal correlation')
  # New case and coverage contracts use the SAME dedicated synthetic database.
  await pool.execute("CREATE TABLE bgp_anomalies(time timestamptz,event_id uuid,event_type text,affected_prefix text,origin_asn bigint,expected_asn bigint,source text,raw_data jsonb)")
  await pool.execute("INSERT INTO bgp_anomalies VALUES($1,$2,'bgp_hijack','198.18.10.0/24',64531,64530,'ris/rrc11','{\"as_path\":[64550,64510,64531],\"peer_asn\":64550}')",base,uuid.uuid4())
  await pool.execute("INSERT INTO bgp_anomalies VALUES($1,$2,'bgp_hijack','198.18.99.0/24',64531,64530,'ris/rrc11','{}')",base,uuid.uuid4())
  for i in range(12):await event(510+i,base+timedelta(seconds=30+i),'198.18.10.0/24',{'before':{'present':True,'path':[64550,64510,64520,64530]},'after':{'present':True,'path':[64550,64510,64521,64530]}})
  await pool.execute("UPDATE routing_diagnostic_cursors SET sequence=600 WHERE observer='qa-a'")
  direction={'pair':{'id':'qa-pair','family':4,'measurement_id':99,'trace_measurement_id':100},'source':{'probe':101,'as_v4':64510,'as_v6':64510,'country':'US','city':'QA source','region':'North America'},'target':{'probe':102,'ip_v4':'198.18.10.1','ip_v6':'2001:db8::1','country':'US','city':'QA target','region':'North America'},'target_asn':64530,'collection_state':'observations_available','observed_at':(base+timedelta(seconds=2)).isoformat(),'current':{'median_of_sample_medians_ms':0,'loss_percent':0,'sent':9,'received':9}}
  wrong=json.loads(json.dumps(direction));wrong['pair']['id']='wrong-prefix';wrong['target']['ip_v4']='198.18.99.1'
  current=json.loads(json.dumps(direction));current['pair']['id']='wrong-time';current['observed_at']=now.isoformat()
  val={'state':'current_snapshot_only','checked_at':now.isoformat(),'items':[{'origin_asn':64530,'state':'available','validation':{'status':'valid','generated_at':now.isoformat(),'checked_at':now.isoformat()}}]}
  case_app=FastAPI();case_app.include_router(incidents.router);case_app.include_router(visibility.router)
  with patch.object(incidents,'get_pg_pool',AsyncMock(return_value=pool)),patch.object(incidents,'get_redis',AsyncMock(return_value=cache)),patch.object(diagnostics,'get_pg_pool',AsyncMock(return_value=pool)),patch.object(diagnostics,'get_redis',AsyncMock(return_value=cache)),patch.object(visibility,'get_pg_pool',AsyncMock(return_value=pool)),patch.object(visibility,'get_redis',AsyncMock(return_value=cache)),patch.object(incidents,'mesh_data',AsyncMock(return_value={'directions':[direction,wrong,current]})),patch.object(visibility,'mesh_data',AsyncMock(return_value={'directions':[direction,wrong,current]})),patch.object(incidents,'validation_context',AsyncMock(return_value=val)):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case_app),base_url='http://qa') as client:
    async def get_case(params,code=200):
     r=await client.get('/incidents/case',params=params);assert r.status_code==code,(r.status_code,r.text[:800]);return r.json()
    cq={'event_id':ids[0],'asn':64510,'resource':'198.18.10.0/24','limit':2}
    pending=await pool.acquire();tx=pending.transaction();await tx.start();delayed2=uuid.uuid4()
    await pending.execute("INSERT INTO routing_diagnostic_events(event_id,observer,sequence,ordinal,epoch,event_at,received_at,prefix,kind,involved_asns,evidence) VALUES($1,'qa-a',601,0,1,$2,$3,'198.18.10.0/24','path_change',ARRAY[64510,64530]::bigint[],'{\"before\":{\"present\":true,\"path\":[64510,64530]}}')",delayed2,base+timedelta(seconds=100),now)
    await pending.execute("UPDATE routing_diagnostic_cursors SET sequence=601 WHERE observer='qa-a'")
    first=await get_case(cq);assert first['anchor']['event_id']==ids[0] and first['timeline']['next_cursor']
    assert len(first['sampled']['items'])==1 and len(first['measurements']['items'])==1
    assert first['measurements']['items'][0]['pair']['id']=='qa-pair' and first['validation']['state']=='current_snapshot_only'
    assert first['validation']['items'][0]['validation']['generated_at']>first['anchor']['event_at']
    await tx.commit();await pool.release(pending);pending=None
    seen=[];page=first
    while True:
     seen.extend(e['event_id'] for e in page['timeline']['items']);assert page['case_id']==first['case_id'] and page['as_of']==first['as_of'] and page['expires_at']==first['expires_at']
     assert page['sampled']==first['sampled'] and page['validation']==first['validation']
     if not page['timeline']['next_cursor']:break
     page=await get_case(cq|{'case_id':first['case_id'],'cursor':page['timeline']['next_cursor']})
    assert len(seen)==18 and len(set(seen))==18 and str(delayed2) not in seen,(len(seen),seen)
    await get_case(cq|{'case_id':first['case_id'],'asn':64599},422)
    await get_case(cq|{'resource':'198.18.99.0/24'},422);await get_case(cq|{'family':'6'},422)
    await get_case(cq|{'cursor':first['timeline']['next_cursor']},422)
    other=await get_case(cq)
    await get_case(cq|{'case_id':other['case_id'],'cursor':first['timeline']['next_cursor']},422)
    await pool.execute('UPDATE routing_diagnostic_policy SET retention_hours=24 WHERE id')
    await get_case(cq|{'case_id':first['case_id']},409)
    await pool.execute('UPDATE routing_diagnostic_policy SET retention_hours=336 WHERE id')
    saved=json.loads(cache.values['noc:incident-case:'+first['case_id']]);saved['expires_at']=(now-timedelta(seconds=1)).isoformat();cache.values['noc:incident-case:'+first['case_id']]=json.dumps(saved)
    await get_case(cq|{'case_id':first['case_id']},409)
    aggregate=await pool.fetchval("SELECT event_id FROM routing_diagnostic_events WHERE candidate='withdrawal_surge' LIMIT 1")
    a=await get_case({'event_id':str(aggregate),'asn':64530,'resource':'198.18.10.0/24','limit':50})
    assert all(not e['recorded_prefixes'] or e['recorded_prefixes']==['198.18.10.0/24'] for e in a['timeline']['items'])
    checks.append('Incident cases: exact 13-day anchor/victim; same-prefix/time measured correlation excludes wrong IP/current measurement; current RPKI clock; frozen sampled/validation context; full 18-row pinned pagination; delayed commit absent; wrong scope/family/foreign cursor/expiry/retention refusal; correctly scoped aggregate impact')
    async def visible(params,code=200):
     r=await client.get('/visibility',params=params);assert r.status_code==code,(r.status_code,r.text[:800]);return r.json()
    for backend in ['0','1']:
     os.environ['INET_INCREMENTAL_CATALOGUE']=backend;cache.values={}
     v=await visible({'asn':64510,'resource':'198.18.0.1'})
     cells={c['observer']:c for c in v['routing_cells']};assert cells['qa-a']['sample_prefix']=='198.18.0.0/25' and cells['qa-a']['state']=='resource_asn_disagreement'
     assert cells['qa-b']['sample_prefix']=='198.18.0.0/24' and cells['qa-b']['state']=='observed'
     assert {c['family'] for c in v['routing_cells']}=={4} and v['global_health'] is None and v['global_coverage_percent'] is None
     v=await visible({'asn':64510});assert {(c['observer'],c['family']) for c in v['routing_cells'] if c['state']=='observed'}=={('qa-a',4),('qa-a',6),('qa-b',4)}
     v=await visible({'asn':64599});assert {(c['observer'],c['family']) for c in v['routing_cells'] if c['state']=='observed'}=={('qa-a',4)}
     v=await visible({'asn':4294967294});assert all(c['state']=='not_observed_in_view' for c in v['routing_cells'])
     v=await visible({'resource':'198.18.10.0/24'});assert all(d['target']['ip_v4']=='198.18.10.1' for d in v['performance']['directions'])
     await visible({'resource':'198.18.0.1','family':'6'},422);await visible({'collector':'invented'},422)
    cache.values={}
    with patch.object(visibility,'mesh_data',AsyncMock(side_effect=RuntimeError('Fixture provider unavailable'))):
     v=await visible({'asn':64520});assert v['routing_cells'] and v['performance']['state']=='collection_unavailable'
    checks.append('Measurement-source failure leaves scoped routing visibility available with explicit unknown performance')
    os.environ['INET_INCREMENTAL_CATALOGUE']='1';cache.values={}
    v=await visible({'resource':'198.18.0.1'});pinned=v['generation']['id'];await visible({'resource':'198.18.0.1','generation_id':pinned})
    await pool.execute('UPDATE inet_incremental.publications SET ready=false WHERE id=$1',pinned-OFFSET)
    await visible({'resource':'198.18.0.1','generation_id':pinned},409)
    await pool.execute('UPDATE inet_incremental.publications SET ready=true WHERE id=$1',pinned-OFFSET)
    checks.append('Resource visibility: both catalogue engines; LPM before ASN; family N/A; explicit absence; actual target IP measurement filtering; null global denominator; invalid scope and expired cached generation refusal')

  # Real HTTP provider adapter with a controlled upstream; no production data or active probe creation.
  original_client=httpx.AsyncClient;calls=[];fault={'mode':''};t=int(now.replace(second=0,microsecond=0).timestamp())-60
  def upstream(request):
   calls.append((request.method,str(request.url)));mode=fault['mode'];path=request.url.path
   if mode=='timeout':raise httpx.ReadTimeout('Fixture timeout',request=request)
   if mode=='large':return httpx.Response(200,content=b' '*(2*1024**2+1))
   probe={'id':101,'asn_v4':64599 if mode=='probe_changed' else 64510,'is_public':True,'status':{'id':1,'name':'Connected'},'country_code':'US'}
   kind=request.url.params.get('type') or fault.get('kind','traceroute')
   meta={'id':123,'type':kind,'af':4,'is_public':mode!='private','target_asn':64530,'target_ip':'8.8.8.8','interval':900,'description':'Fixture'}
   if path.endswith('/probes/'):return httpx.Response(200,json={'results':[probe],'next':None})
   if path.endswith('/probes/101/'):return httpx.Response(200,json=probe)
   if path.endswith('/measurements/'):
    assert request.url.params['current_probes']=='101' and request.url.params['target_asn']=='64530'
    return httpx.Response(200,json={'results':[meta],'next':None})
   if path.endswith('/measurements/123/'):return httpx.Response(200,json=meta)
   if path.endswith('/results/'):
    assert request.url.params['probe_ids']=='101';rows=[]
    for i in range(2):
     row={'msm_id':123,'prb_id':102 if mode=='wrong_probe' else 101,'af':4,'type':kind,'timestamp':t-i*60,'dst_addr':'8.8.8.8'}
     if kind=='ping':row.update(sent=3,rcvd=3,avg=0)
     else:row['result']=[{'hop':1,'result':[{'x':'*'}]},{'hop':3,'result':[{'from':'8.8.8.8','rtt':5+i}]}]
     rows.append(row)
    return httpx.Response(200,json=rows)
   raise AssertionError(path)
  transport=httpx.MockTransport(upstream)
  def fake_client(*args,**kwargs):kwargs['transport']=transport;return original_client(*args,**kwargs)
  with patch.object(measured,'get_pg_pool',AsyncMock(return_value=pool)),patch.object(measured,'get_redis',AsyncMock(return_value=cache)),patch.object(measured.httpx,'AsyncClient',fake_client):
   async with original_client(transport=httpx.ASGITransport(app=app),base_url='http://qa') as client:
    async def get(path,params,code=200):
     r=await client.get('/paths/'+path,params=params);assert r.status_code==code,(path,r.status_code,r.text[:400]);return r.json()
    q={'source_asn':64510,'destination_asn':64530,'family':'4','probe_id':101,'measurement_id':123}
    p=await get('probes',{'source_asn':64510});assert p['probes'][0]['id']==101
    m=await get('measurements',q);assert m['measurements'][0]['target']=='8.8.8.8'
    v=await get('measurement',q);assert v['observations'][0]['hops'][1]['state']=='not_recorded'
    assert v['observations'][0]['hops'][2]['responders'][0]['mapping']['origin_asns']==[64530]
    assert v['comparison']['state']=='incomplete_comparison';n=len(calls);await get('measurement',q);assert len(calls)==n
    for mode,code in [('probe_changed',409),('private',409),('wrong_probe',503),('timeout',503),('large',503)]:
     cache.values.clear();fault['mode']=mode;await get('measurement',q,code)
    fault.update(mode='',kind='ping');cache.values.clear();v=await get('measurement',q|{'kind':'ping'});assert v['observations'][0]['average_rtt_ms']==0 and v['observations'][0]['loss_percent']==0
    assert all(method=='GET' and url.startswith('https://atlas.ripe.net/api/v2/') for method,url in calls)
    await get('measurement',q|{'until':now.replace(tzinfo=None).isoformat()},422)
    await get('measurement',q|{'measurement_id':0},422)
    checks.append('Public probe/measurement discovery and identities, actual-hop/unknown-TTL/current-origin hints, cached reads, ping zero RTT, wrong/private/upstream-timeout/oversize refusals; all provider requests GET-only')
  print(json.dumps({'pass':True,'checks':checks,'provider_get_requests':len(calls),'browser_fixture':{'event_id':ids[0],'asn':64510,'resource':'198.18.10.0/24','path_source':64510,'path_destination':64530}},indent=2))
 finally:
  if pending:
   await tx.rollback();await pool.release(pending)
  await pool.close()


asyncio.run(main())
