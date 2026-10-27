"""Rollback-only DEV replay: separate route contents from indexed last receipt.

Prototype only. No migration, source-writer or production index changes. All raw
journal input remains private and unchanged; reconstructed current rows must match.
"""
import asyncio,ast,hashlib,json,socket,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(ROOT/'collectors')]
import asyncpg
from collectors.routing_model import ris_changes
source=ROOT/'collectors/collectors/routing_store.py'
nodes=[x for x in ast.parse(source.read_text()).body if (isinstance(x,ast.FunctionDef) and x.name in ['route_row','consolidate_routes']) or (isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='UPSERT' for t in x.targets))]
helpers={'json':json};exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),helpers)
FIELDS='observer,prefix,epoch,generation,event_at,rank,event_order,present,origin_asn,path,attrs,evidence_id'
SPLIT='''WITH winner AS (
 INSERT INTO inet_receipt_trial.receipts(observer,prefix,epoch,generation,event_at,rank,event_order,evidence_id)
 VALUES($1,$2::text::cidr,$3,$4,$5,$6,$7,$12)
 ON CONFLICT(observer,prefix) DO UPDATE SET epoch=excluded.epoch,generation=excluded.generation,event_at=excluded.event_at,rank=excluded.rank,event_order=excluded.event_order,evidence_id=excluded.evidence_id
 WHERE excluded.epoch>receipts.epoch OR (excluded.epoch=receipts.epoch AND (excluded.event_at,excluded.rank,excluded.event_order)>(receipts.event_at,receipts.rank,receipts.event_order))
 RETURNING observer,prefix)
 INSERT INTO inet_receipt_trial.contents(observer,prefix,present,origin_asn,path,attrs)
 SELECT observer,prefix,$8,$9,$10::jsonb,$11::jsonb FROM winner
 ON CONFLICT(observer,prefix) DO UPDATE SET present=excluded.present,origin_asn=excluded.origin_asn,path=excluded.path,attrs=excluded.attrs
 WHERE (contents.present,contents.origin_asn,contents.path,contents.attrs) IS DISTINCT FROM (excluded.present,excluded.origin_asn,excluded.path,excluded.attrs)'''
JOIN='SELECT m.observer,m.prefix,m.epoch,m.generation,m.event_at,m.rank,m.event_order,c.present,c.origin_asn,c.path,c.attrs,m.evidence_id FROM inet_receipt_trial.receipts m JOIN inet_receipt_trial.contents c USING(observer,prefix)'
async def main():
 assert socket.gethostname()=='inet-health-dev1'
 data=json.loads(Path('/tmp/inet-write-replay-input.json').read_text());batches=[];frames=0;sha=hashlib.sha256()
 for o in data['observers']:
  state={'epoch':1,'generation':uuid.UUID(int=1)};pending=[]
  for frame in reversed(data['frames'][o['id']]):
   raw=json.loads(frame['payload']);sha.update(frame['payload'].encode());frames+=1
   _,_,changes=ris_changes(raw,dict(o));kind=raw['data']['type']
   if kind in ['OPEN','NOTIFICATION'] or (kind=='RIS_PEER_STATE' and raw['data'].get('state')=='down'):state['epoch']+=1;state['generation']=uuid.UUID(int=state['epoch'])
   pending.extend(helpers['route_row'](o['id'],r,state,2,f"{frame['sequence']:020d}:{i:06d}") for i,r in enumerate(changes))
   if frames%64==0:batches.append(pending);pending=[]
  if pending:batches.append(pending)
 selected=[];total=0
 for b in batches:
  if total+len(b)>15000 and selected:break
  selected.append(helpers['consolidate_routes'](b));total+=len(b)
 c=await asyncpg.connect(database='inethealth',user='postgres',host='/var/run/postgresql',command_timeout=120)
 outer=c.transaction();await outer.start();results=[];expected=None
 try:
  await c.execute("SET LOCAL statement_timeout='90s'; SET LOCAL lock_timeout='1s'; SET LOCAL jit=off")
  await c.execute('CREATE SCHEMA inet_receipt_trial')
  for variant in ['baseline','split','baseline_repeat','split_repeat']:
   tx=c.transaction();await tx.start()
   if variant.startswith('baseline'):
    await c.execute('CREATE TABLE inet_receipt_trial.routes(observer text NOT NULL,prefix cidr NOT NULL,epoch bigint NOT NULL,generation uuid NOT NULL,event_at timestamptz NOT NULL,rank integer NOT NULL,event_order text NOT NULL,present boolean NOT NULL,origin_asn bigint,path jsonb NOT NULL,attrs jsonb NOT NULL,evidence_id text NOT NULL)')
    for index in data['indexes']:await c.execute(index['indexdef'].replace('ON public.routing_routes','ON inet_receipt_trial.routes'))
    sql=helpers['UPSERT'].replace('routing_routes','inet_receipt_trial.routes');read='SELECT '+FIELDS+' FROM inet_receipt_trial.routes'
   else:
    await c.execute('''CREATE TABLE inet_receipt_trial.receipts(observer text,prefix cidr,epoch bigint,generation uuid,event_at timestamptz,rank integer,event_order text,evidence_id text,PRIMARY KEY(observer,prefix));
    CREATE INDEX receipt_recent ON inet_receipt_trial.receipts(event_at DESC,observer,prefix);
    CREATE INDEX receipt_observer_time ON inet_receipt_trial.receipts(observer,event_at);
    CREATE TABLE inet_receipt_trial.contents(observer text,prefix cidr,present boolean,origin_asn bigint,path jsonb,attrs jsonb,PRIMARY KEY(observer,prefix));
    CREATE INDEX content_prefix ON inet_receipt_trial.contents USING gist(prefix inet_ops);
    CREATE INDEX content_origin ON inet_receipt_trial.contents(observer,origin_asn) WHERE present;''')
    sql=SPLIT;read=JOIN
   wal=await c.fetchval('SELECT pg_current_wal_insert_lsn()::text');at=time.monotonic()
   for b in selected:await c.executemany(sql,b)
   result={'variant':variant,'seconds':round(time.monotonic()-at,4),'cluster_wal_bytes':int(await c.fetchval('SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),$1::text::pg_lsn)',wal))}
   rows=await c.fetch('SELECT * FROM ('+read+') current ORDER BY observer,prefix');state=[dict(r) for r in rows]
   if expected is None:expected=state
   assert state==expected,'Current route state/provenance mismatch'
   result['current_rows']=len(rows)
   result['table_writes']=[dict(r) for r in await c.fetch("SELECT relname,n_tup_ins,n_tup_upd,n_tup_hot_upd FROM pg_stat_xact_user_tables WHERE schemaname='inet_receipt_trial' ORDER BY relname")]
   # Retain the chronological btree; qualify the additional point joins.
   table='routes' if variant.startswith('baseline') else 'receipts'
   await c.execute('ANALYZE inet_receipt_trial.'+table)
   if table=='receipts':await c.execute('ANALYZE inet_receipt_trial.contents')
   ordered=read+(' ORDER BY event_at DESC,observer,prefix LIMIT 100' if table=='routes' else ' ORDER BY m.event_at DESC,m.observer,m.prefix LIMIT 100')
   plan=json.loads(await c.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+ordered))[0]
   result['recency_read_ms']=plan['Execution Time'];result['read_plan']=plan['Plan'];results.append(result)
   print('RECEIPT_SPLIT_ARM',json.dumps({k:v for k,v in result.items() if k!='read_plan'}),flush=True)
   await tx.rollback()
  report={'pass':True,'frames':frames,'raw_operations':total,'consolidated_operations':sum(map(len,selected)),'input_sha256':sha.hexdigest(),'all_current_rows_and_provenance_identical':True,'results':results,'scope':'small captured replay, rollback-only DEV; no production change; WAL is cluster-wide; read and larger-state qualification still needed'}
  Path('/tmp/inet-receipt-split-qualification.json').write_text(json.dumps(report,indent=2,default=str));print('PASS_RECEIPT_SPLIT_EXACT_STATE',flush=True)
 finally:await outer.rollback();await c.close()
asyncio.run(main())
