"""Current-view batching must match the SQL winner without changing the journal."""
import ast,random,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
source=Path(__file__).resolve().parents[1]/'collectors/collectors/routing_store.py'
node=next(x for x in ast.parse(source.read_text()).body if isinstance(x,ast.FunctionDef) and x.name=='consolidate_routes')
scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
consolidate=scope['consolidate_routes'];T=datetime(2026,1,1,tzinfo=timezone.utc)
def row(prefix='192.0.2.0/24',epoch=1,t=0,rank=2,order='1',present=True,observer='test'):
 return (observer,prefix,epoch,'g'+str(epoch),T+timedelta(seconds=t),rank,order,present,64500,'[64500]','{}','proof')
def sequential(rows,initial=()):
 state={r[:2]:r for r in initial}
 for r in rows:
  old=state.get(r[:2])
  if old is None or (r[2],r[4],r[5],r[6])>(old[2],old[4],old[5],old[6]):state[r[:2]]=r
 return state
class ConsolidationTests(unittest.TestCase):
 def test_withdraw_announce_retains_final_state_and_input_transitions(self):
  values=[row(t=1),row(t=2,present=False),row(t=3)];before=list(values)
  self.assertEqual(consolidate(values),[values[-1]]);self.assertEqual(values,before)
 def test_epoch_reset_wins_even_with_older_event_time(self):
  values=[row(t=100),row(epoch=2,t=1),row(epoch=1,t=200)]
  self.assertEqual(consolidate(values),[values[1]])
 def test_event_rank_order_and_equal_first_wins(self):
  values=[row(rank=1),row(rank=2,order='2'),row(rank=2,order='1'),row(rank=2,order='2',present=False)]
  self.assertEqual(consolidate(values),[values[1]])
 def test_observer_prefix_isolation_and_empty(self):
  values=[row(),row(observer='other'),row(prefix='2001:db8::/32')]
  self.assertEqual(consolidate(values),values);self.assertEqual(consolidate([]),[])
 def test_seeded_random_replays_match_existing_state(self):
  rng=random.Random(731)
  for _ in range(100):
   values=[row(prefix=f'192.0.{rng.randrange(16)}.0/24',epoch=rng.randrange(1,4),t=rng.randrange(100),rank=rng.randrange(3),order=str(rng.randrange(10)),present=bool(rng.randrange(2))) for _ in range(200)]
   initial=[row(prefix=f'192.0.{i}.0/24',t=50) for i in range(16)]
   self.assertEqual(sequential(values,initial),sequential(consolidate(values),initial))

class JournalPreservationTests(unittest.IsolatedAsyncioTestCase):
 async def test_all_transitions_are_journaled_with_one_current_state_write(self):
  import sys,uuid,json
  from unittest.mock import AsyncMock
  sys.path.insert(0,str(source.parents[1]))
  from collectors.routing_model import ris_changes
  tree=ast.parse(source.read_text())
  selected=[x for x in tree.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name in ['consolidate_routes','route_row','commit_messages'] or isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='UPSERT' for t in x.targets)]
  class Context:
   def __init__(self,value):self.value=value
   async def __aenter__(self):return self.value
   async def __aexit__(self,*args):pass
  class Conn:
   def __init__(self):
    self.keys=set();self.execute=AsyncMock();self.executemany=AsyncMock()
   def transaction(self):return Context(self)
   async def fetchrow(self,*args):return {'sequence':0,'messages':0,'updates':0,'epoch':1,'generation':uuid.UUID(int=1),'peer_state':'observed','baseline_state':'required','reset_at':None,'event_at':None,'received_at':None,'dedup_floor_at':None}
   async def fetchval(self,sql,observer,ident,received,at,seq):
    if ident in self.keys:return None
    self.keys.add(ident);return seq
  conn=Conn()
  class Pool:
   def acquire(self):return Context(conn)
  scope={'json':json,'uuid':uuid,'ris_changes':ris_changes,'get_pg_pool':AsyncMock(return_value=Pool())}
  exec(compile(ast.Module(body=selected,type_ignores=[]),str(source),'exec'),scope)
  observer={'id':'test','collector':'rrc00','peer_ip':'192.0.2.1','peer_asn':64500}
  frames=[]
  for i in range(30):
   data={'host':'rrc00.ripe.net','peer':'192.0.2.1','peer_asn':64500,'type':'UPDATE','timestamp':(T+timedelta(seconds=i)).timestamp(),'id':str(i),'path':[64500,64501],'announcements':[{'prefixes':['192.0.2.0/24']}] if i%2==0 else [],'withdrawals':[] if i%2==0 else ['192.0.2.0/24']}
   frames.append(({'data':data},T+timedelta(seconds=i)))
  frames.append(frames[-1])
  await scope['commit_messages'](observer,frames)
  journal=[c for c in conn.execute.call_args_list if 'INSERT INTO routing_messages(' in c.args[0]]
  self.assertEqual(len(journal),30);self.assertEqual([c.args[5] for c in journal],list(range(1,31)))
  self.assertEqual(conn.executemany.await_count,1)
  winners=conn.executemany.call_args.args[1];self.assertEqual(len(winners),1);self.assertFalse(winners[0][7])
  observed=next(c for c in conn.execute.call_args_list if 'UPDATE routing_observers SET epoch=' in c.args[0])
  self.assertEqual(observed.args[4:7],(30,30,30))

if __name__=='__main__':unittest.main()
