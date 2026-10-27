"""Build owned journal sequence indexes concurrently, one partition at a time."""
import argparse,asyncio,json,re,socket,sys,time
from pathlib import Path
sys.path[:0]=['/opt/inet-journal-runtime/vendor','/opt/inet-write-opt/qa-vendor']
import asyncpg
PARENT='routing_messages_diagnostic_sequence'
def ident(value):
 if not re.fullmatch('[a-z0-9_]+',value):raise ValueError('Unexpected identifier')
 return '"public"."'+value+'"'
async def main():
 p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--expected-host',required=True);args=p.parse_args()
 assert (args.database,args.expected_host) in [('inethealth','inet-health-prod'),('inet_diag_qa_20261027','inet-health-dev1')]
 assert socket.gethostname()==args.expected_host
 root=Path('/tmp/inet-diagnostic-index-'+args.database);root.mkdir(exist_ok=True);state=root/'owned.json'
 owned=json.loads(state.read_text()) if state.exists() else {}
 c=await asyncpg.connect(database=args.database,user='postgres',host='/var/run/postgresql',command_timeout=130)
 assert await c.fetchval('SELECT current_database()')==args.database
 await c.execute("SET lock_timeout='1s'");await c.execute("SET statement_timeout='120s'");await c.execute('SET max_parallel_maintenance_workers=0')
 async def index(name,table,parent=False):
  statement='CREATE INDEX '+('' if parent else 'CONCURRENTLY ')+('"'+name+'"')+' ON '+('ONLY ' if parent else '')+ident(table)+'(observer,sequence)'
  for attempt in range(6):
   row=await c.fetchrow('''SELECT i.indisvalid,i.indrelid::regclass::text AS table_name,
    ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(attnum,n) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.n) columns,
    i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=2 AND i.indnatts=2 AND NOT i.indisunique AND am.amname='btree' simple
    FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid JOIN pg_am am ON am.oid=ic.relam WHERE i.indexrelid=to_regclass($1)''','public.'+name)
   if row:
    assert row['table_name'] in [table,'public.'+table] and row['simple'] and list(row['columns'])==['observer','sequence'],('Index name has a different definition',name)
    if row['indisvalid'] or parent:return
    assert owned.get(name)==table,'Refuse removal of an unowned incomplete index'
    await c.execute('DROP INDEX CONCURRENTLY '+ident(name))
   owned[name]=table;state.write_text(json.dumps(owned,indent=2))
   try:await c.execute(statement);break
   except (asyncpg.LockNotAvailableError,asyncpg.QueryCanceledError):
    if attempt==5:
     bad=await c.fetchval('SELECT NOT indisvalid FROM pg_index WHERE indexrelid=to_regclass($1)','public.'+name)
     if bad and not parent:await c.execute('DROP INDEX CONCURRENTLY '+ident(name))
     raise
    await asyncio.sleep(3)
  print('INDEX_CREATED',name,flush=True)
 try:
  kind=await c.fetchval("SELECT relkind::text FROM pg_class WHERE oid='public.routing_messages'::regclass")
  if kind=='p':
   await index(PARENT,'routing_messages',True)
   for row in await c.fetch("SELECT t.relname FROM pg_inherits i JOIN pg_class t ON t.oid=i.inhrelid WHERE inhparent='public.routing_messages'::regclass ORDER BY t.relname"):
    table=row['relname'];name=table+'_diag_sequence'
    if not await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_inherits WHERE inhparent=$1::regclass AND inhrelid=to_regclass($2))','public.'+PARENT,'public.'+name):
     await index(name,table)
     await c.execute('ALTER INDEX '+ident(PARENT)+' ATTACH PARTITION '+ident(name))
    print('INDEX_READY',table,flush=True)
  else:await index(PARENT,'routing_messages')
  assert await c.fetchval('SELECT indisvalid FROM pg_index WHERE indexrelid=$1::regclass','public.'+PARENT)
  print('JOURNAL_SEQUENCE_INDEX_READY',flush=True)
 finally:await c.close()
asyncio.run(main())
