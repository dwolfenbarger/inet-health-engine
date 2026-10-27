"""UAT rollback-only replay: identical captured input, exact state, per-statement WAL."""
import asyncio,ast,json,socket,sys,time,uuid,hashlib
from pathlib import Path
sys.path[:0]=['/opt/inet-journal-runtime/vendor',str(Path(__file__).resolve().parents[1]/'collectors')]
import asyncpg
from collectors.routing_model import ris_changes
# Load only pure helpers and SQL, avoiding application credentials or pool creation.
file=Path(__file__).resolve().parents[1]/'collectors/collectors/routing_store.py'
nodes=[x for x in ast.parse(file.read_text()).body if (isinstance(x,ast.FunctionDef) and x.name in ['route_row','consolidate_routes']) or (isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='UPSERT' for t in x.targets))]
scope={'json':json};exec(compile(ast.Module(body=nodes,type_ignores=[]),str(file),'exec'),scope)
async def main():
 assert socket.gethostname() in {'inet-health-uat','inet-health-dev1'},'Development only'
 data=json.loads(Path('/tmp/inet-write-replay-input.json').read_text())
 c=await asyncpg.connect(database='inethealth',user='postgres',host='/var/run/postgresql',command_timeout=60)
 tx=c.transaction();await tx.start()
 try:
  await c.execute("SET LOCAL lock_timeout='2s'; SET LOCAL statement_timeout='30s'")
  # A logged, transaction-local test relation: production and UAT source rows are never changed.
  await c.execute('CREATE SCHEMA inet_write_replay_test')
  await c.execute('''CREATE TABLE inet_write_replay_test.routes(observer text NOT NULL,prefix cidr NOT NULL,epoch bigint NOT NULL,generation uuid NOT NULL,event_at timestamptz NOT NULL,rank integer NOT NULL,event_order text NOT NULL,present boolean NOT NULL,origin_asn bigint,path jsonb NOT NULL,attrs jsonb NOT NULL,evidence_id text NOT NULL)''')
  for index in data['indexes']:
   await c.execute(index['indexdef'].replace('ON public.routing_routes','ON inet_write_replay_test.routes'))
  sql=scope['UPSERT'].replace('routing_routes','inet_write_replay_test.routes')+' RETURNING 1'
  observers=data['observers']
  batches=[];frames_count=0;input_hash=hashlib.sha256()
  for o in observers:
   frames=data['frames'][o['id']]
   state={'epoch':1,'generation':uuid.UUID(int=1)};pending=[]
   for frame in reversed(frames):
    raw=json.loads(frame['payload']);input_hash.update(frame['payload'].encode());frames_count+=1
    at,ident,changes=ris_changes(raw,dict(o));kind=raw['data']['type']
    if kind in ['OPEN','NOTIFICATION'] or (kind=='RIS_PEER_STATE' and raw['data'].get('state')=='down'):
     state['epoch']+=1;state['generation']=uuid.UUID(int=state['epoch'])
    pending.extend(scope['route_row'](o['id'],item,state,2,f"{frame['sequence']:020d}:{i:06d}") for i,item in enumerate(changes))
    if frames_count%64==0:batches.append(pending);pending=[]
   if pending:batches.append(pending)
  # Bound the write benchmark while retaining complete input batches.
  selected=[];total=0
  for b in batches:
   if total+len(b)>15000 and selected:break
   selected.append(b);total+=len(b)
  print('INPUT',json.dumps({'captured_frames':frames_count,'selected_batches':len(selected),'route_operations':total,'sha256':input_hash.hexdigest()}),flush=True)
  results={};states={}
  for name,compact in [('baseline',False),('consolidated',True),('baseline_repeat',False),('consolidated_repeat',True)]:
   sub=c.transaction();await sub.start();t=time.monotonic();metrics={'statements':0,'row_versions':0,'wal_bytes':0,'wal_records':0,'shared_dirtied_blocks':0,'shared_read_blocks':0,'shared_hit_blocks':0}
   for b in selected:
    values=scope['consolidate_routes'](b) if compact else b
    for value in values:
     raw=await c.fetchval('EXPLAIN (ANALYZE, WAL, BUFFERS, FORMAT JSON) '+sql,*value)
     p=json.loads(raw)[0]['Plan'];metrics['statements']+=1;metrics['row_versions']+=p['Actual Rows']
     for key,field in [('wal_bytes','WAL Bytes'),('wal_records','WAL Records'),('shared_dirtied_blocks','Shared Dirtied Blocks'),('shared_read_blocks','Shared Read Blocks'),('shared_hit_blocks','Shared Hit Blocks')]:metrics[key]+=p.get(field,0)
   metrics['seconds']=round(time.monotonic()-t,3)
   records=await c.fetch('SELECT observer,prefix::text,epoch,generation,event_at,rank,event_order,present,origin_asn,path,attrs,evidence_id FROM inet_write_replay_test.routes ORDER BY observer,prefix')
   states[name]=[dict(r) for r in records];metrics['final_routes']=len(records);results[name]=metrics
   await sub.rollback();print(name,json.dumps(metrics),flush=True)
  assert all(states[k]==states['baseline'] for k in states),'State mismatch'
  print('PASS_EXACT_STATE_AND_PROVENANCE',json.dumps(results),flush=True)
 finally:await tx.rollback();await c.close()
asyncio.run(main())
