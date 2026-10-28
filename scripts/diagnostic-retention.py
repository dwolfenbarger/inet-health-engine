"""Owned daily diagnostic partitions, adoption without a history copy, and rollback.
Run as postgres. Only explicitly named application tables are changed.
"""
import argparse,asyncio,datetime,json,re,socket,sys
from pathlib import Path
sys.path.insert(0,'/opt/inet-journal-runtime/vendor')
import asyncpg
UTC=datetime.timezone.utc
PARENT='routing_diagnostic_events';NEW=PARENT+'_ret14';BOOT='rde14_bootstrap';RETIRED='rde14_retired'
INDEXES={
 'identity':('UNIQUE','event_id,event_at',None),
 'prefix':('', 'prefix,event_at DESC,id DESC',None),
 'time':('', 'event_at DESC',None),
 'candidate':('', 'event_at DESC','candidate IS NOT NULL'),
 'asns':('GIN','involved_asns',None),
 'network':('GIST','prefix inet_ops',None),
 'surge':('', 'event_at DESC,id DESC',"candidate='withdrawal_surge'"),
}
def ident(v):
 assert re.fullmatch('[a-z][a-z0-9_]{0,62}',v),v
 return '"'+v+'"'
def day(v):return v.astimezone(UTC).replace(hour=0,minute=0,second=0,microsecond=0)
def stamp(v):return v.astimezone(UTC).isoformat()
async def main(args):
 host=socket.gethostname();assert (host=='inet-health-prod' and args.database=='inethealth') or (host=='inet-health-dev1' and args.database.startswith('inet_ret14_qa_'))
 assert not args.qa_now or (host=='inet-health-dev1' and args.database.startswith('inet_ret14_qa_'))
 c=await asyncpg.connect(database=args.database,user='postgres',host='/var/run/postgresql',command_timeout=130)
 root=Path('/var/lib/postgresql/inet-diagnostic-retention-'+args.database) if host=='inet-health-prod' else Path('/tmp/inet-ret14-'+args.database);root.mkdir(exist_ok=True);statefile=root/'adoption.json'
 def save(v):
  tmp=statefile.with_suffix('.new');tmp.write_text(json.dumps(v,indent=2,default=str));tmp.replace(statefile)
 try:
  await c.execute("SET lock_timeout='200ms'; SET statement_timeout='120s'")
  assert await c.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-diagnostic-retention-v1'))"),'Another retention owner is running'
  now=datetime.datetime.fromisoformat(args.qa_now).astimezone(UTC) if args.qa_now else await c.fetchval('SELECT clock_timestamp()')
  if args.action=='prepare':
   if statefile.exists():state=json.loads(statefile.read_text());assert state['database']==args.database
   else:
    assert await c.fetchval("SELECT relkind::text FROM pg_class WHERE oid='public.routing_diagnostic_events'::regclass")=='r'
    assert not await c.fetchval('SELECT to_regclass($1)',NEW)
    state={'database':args.database,'phase':'preparing','old_oid':await c.fetchval("SELECT 'public.routing_diagnostic_events'::regclass::oid"),'bootstrap_until':stamp(day(now)+datetime.timedelta(days=1))};save(state)
   assert state['phase'] in ['preparing','prepared']
   assert await c.fetchval("SELECT 'routing_diagnostic_events'::regclass::oid")==state['old_oid']
   # LIKE without indexes/checks: the initial range check must not constrain future partitions.
   await c.execute(f'CREATE TABLE IF NOT EXISTS {ident(NEW)} (LIKE {ident(PARENT)} INCLUDING DEFAULTS INCLUDING STORAGE) PARTITION BY RANGE(event_at)')
   for key,(method,columns,predicate) in INDEXES.items():
    name='rde14_'+key;using=' USING '+method.lower() if method in ['GIN','GIST'] else '';unique='UNIQUE ' if method=='UNIQUE' else '';where=' WHERE '+predicate if predicate else ''
    await c.execute(f'CREATE {unique}INDEX IF NOT EXISTS {ident(name)} ON ONLY {ident(NEW)}{using} ({columns}){where}')
   # This extra index supports stable event identity on the new partitioned parent.
   await c.execute("SET lock_timeout='2s';SET statement_timeout='600s'")
   try:
    ix=await c.fetchrow("SELECT i.indisvalid,pg_get_indexdef(i.indexrelid) def FROM pg_index i WHERE i.indexrelid=to_regclass('public.rde14_bootstrap_identity')")
    if ix and not ix['indisvalid']:
     assert ' ON public.routing_diagnostic_events USING btree (event_id, event_at)' in ix['def']
     await c.execute('DROP INDEX CONCURRENTLY rde14_bootstrap_identity');ix=None
    if not ix:await c.execute('CREATE UNIQUE INDEX CONCURRENTLY rde14_bootstrap_identity ON routing_diagnostic_events(event_id,event_at)',timeout=610)
    ix=await c.fetchrow("SELECT i.indisvalid,pg_get_indexdef(i.indexrelid) def FROM pg_index i WHERE i.indexrelid=to_regclass('public.rde14_bootstrap_network')")
    if ix and not ix['indisvalid']:
     assert ' ON public.routing_diagnostic_events USING gist (prefix inet_ops)' in ix['def']
     await c.execute('DROP INDEX CONCURRENTLY rde14_bootstrap_network');ix=None
    if not ix:await c.execute('CREATE INDEX CONCURRENTLY rde14_bootstrap_network ON routing_diagnostic_events USING gist(prefix inet_ops)',timeout=610)
    ix=await c.fetchrow("SELECT i.indisvalid,pg_get_indexdef(i.indexrelid) def FROM pg_index i WHERE i.indexrelid=to_regclass('public.rde14_bootstrap_surge')")
    if ix and not ix['indisvalid']:
     assert " ON public.routing_diagnostic_events USING btree (event_at DESC, id DESC) WHERE (candidate = 'withdrawal_surge'::text)" in ix['def']
     await c.execute('DROP INDEX CONCURRENTLY rde14_bootstrap_surge');ix=None
    if not ix:await c.execute("CREATE INDEX CONCURRENTLY rde14_bootstrap_surge ON routing_diagnostic_events(event_at DESC,id DESC) WHERE candidate='withdrawal_surge'",timeout=610)
   finally:await c.execute("SET lock_timeout='200ms';SET statement_timeout='120s'")
   until=datetime.datetime.fromisoformat(state['bootstrap_until']);assert await c.fetchval('SELECT NOT EXISTS(SELECT 1 FROM routing_diagnostic_events WHERE event_at >= $1)',until)
   exists=await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='routing_diagnostic_events'::regclass AND conname='rde14_bootstrap_bound')")
   if not exists:await c.execute(f"ALTER TABLE {ident(PARENT)} ADD CONSTRAINT rde14_bootstrap_bound CHECK(event_at < '{stamp(until)}'::timestamptz) NOT VALID")
   await c.execute('ALTER TABLE routing_diagnostic_events VALIDATE CONSTRAINT rde14_bootstrap_bound')
   state['phase']='prepared';save(state);print(json.dumps(state));return
  if args.action=='cutover':
   state=json.loads(statefile.read_text());assert state['phase']=='prepared'
   assert await c.fetchval("SELECT 'routing_diagnostic_events'::regclass::oid")==state['old_oid']
   assert await c.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-routing-diagnostics'))"),'Diagnostic writer is not drained'
   until=datetime.datetime.fromisoformat(state['bootstrap_until']);assert now<until
   async with c.transaction():
    await c.execute("SET LOCAL statement_timeout='15s'")
    await c.execute(f'ALTER TABLE {ident(PARENT)} RENAME TO {ident(BOOT)}')
    await c.execute(f'ALTER TABLE {ident(NEW)} RENAME TO {ident(PARENT)}')
    # The validated bound lets ATTACH avoid scanning/re-writing the live history.
    await c.execute(f"ALTER TABLE {ident(PARENT)} ATTACH PARTITION {ident(BOOT)} FOR VALUES FROM (MINVALUE) TO ('{stamp(until)}')")
    for key,child in [('identity','rde14_bootstrap_identity'),('prefix','diagnostic_events_prefix'),('time','diagnostic_events_time'),('candidate','diagnostic_events_candidate'),('asns','diagnostic_events_asns'),('network','rde14_bootstrap_network'),('surge','rde14_bootstrap_surge')]:
     attached=await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_inherits WHERE inhparent=to_regclass($1) AND inhrelid=to_regclass($2))','public.rde14_'+key,'public.'+child)
     if not attached:await c.execute(f'ALTER INDEX {ident("rde14_"+key)} ATTACH PARTITION {ident(child)}')
    await c.execute('ALTER SEQUENCE routing_diagnostic_events_id_seq OWNED BY routing_diagnostic_events.id')
    await c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON routing_diagnostic_events TO inetuser')
    await c.execute('''CREATE TABLE IF NOT EXISTS routing_diagnostic_partitions(name text PRIMARY KEY, upper_bound timestamptz, kind text NOT NULL CHECK(kind IN ('bootstrap','daily','default')), created_at timestamptz NOT NULL DEFAULT now())''')
    await c.execute('INSERT INTO routing_diagnostic_partitions(name,upper_bound,kind) VALUES($1,$2,\'bootstrap\')',BOOT,until)
    await c.execute("ALTER TABLE routing_diagnostic_policy ADD COLUMN IF NOT EXISTS retention_mode text NOT NULL DEFAULT 'row_delete'; ALTER TABLE routing_diagnostic_policy ADD COLUMN IF NOT EXISTS retention_checked_at timestamptz; ALTER TABLE routing_diagnostic_policy ADD COLUMN IF NOT EXISTS retention_error text")
    await c.execute("UPDATE routing_diagnostic_policy SET retention_mode='partitioned',retention_checked_at=now(),retention_error=NULL WHERE id")
   state.update(phase='adopted',adopted_at=stamp(now));save(state)
   # Create current/future children before the first post-adoption writer starts.
  elif args.action=='rollback':
   state=json.loads(statefile.read_text());assert state['phase']=='adopted'
   assert await c.fetchval("SELECT pg_try_advisory_lock(hashtext('inet-routing-diagnostics'))"),'Diagnostic writer is not drained'
   # This rollback stays bounded only during the initial bootstrap range.
   # Later dates require a separately verified merge of later partition evidence.
   assert now<datetime.datetime.fromisoformat(state['bootstrap_until']),'Rollback requires reviewed later-partition merge; no data discarded'
   others=await c.fetch('SELECT name FROM routing_diagnostic_partitions WHERE name<>$1',BOOT)
   for row in others:assert await c.fetchval(f'SELECT NOT EXISTS(SELECT 1 FROM {ident(row["name"])})'),'Later partition contains evidence; no blind rollback'
   async with c.transaction():
    await c.execute(f'ALTER TABLE {ident(PARENT)} DETACH PARTITION {ident(BOOT)}')
    await c.execute(f'ALTER TABLE {ident(PARENT)} RENAME TO {ident(RETIRED)}')
    await c.execute(f'ALTER TABLE {ident(BOOT)} RENAME TO {ident(PARENT)}')
    await c.execute('ALTER TABLE routing_diagnostic_events DROP CONSTRAINT rde14_bootstrap_bound')
    await c.execute('ALTER SEQUENCE routing_diagnostic_events_id_seq OWNED BY routing_diagnostic_events.id')
    await c.execute("UPDATE routing_diagnostic_policy SET retention_mode='row_delete',retention_checked_at=now(),retention_error=NULL WHERE id")
   state.update(phase='rolled_back',rolled_back_at=stamp(now));save(state);print(json.dumps(state));return
  elif args.action!='maintain':raise ValueError(args.action)
  await c.execute("SET statement_timeout='5s'")
  # Only registered children of the exact application parent are eligible.
  assert await c.fetchval("SELECT relkind::text FROM pg_class WHERE oid='public.routing_diagnostic_events'::regclass")=='p'
  state=json.loads(statefile.read_text());assert state['phase']=='adopted'
  policy=await c.fetchrow('SELECT * FROM routing_diagnostic_policy WHERE id');assert policy and 1<=policy['retention_hours']<=336
  children=await c.fetch("SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid WHERE i.inhparent='public.routing_diagnostic_events'::regclass")
  registered={r['name']:dict(r) for r in await c.fetch('SELECT * FROM routing_diagnostic_partitions')}
  assert {r['relname'] for r in children}==set(registered),'Unknown or missing diagnostic child; refusing destructive maintenance'
  cutoff=now-datetime.timedelta(hours=policy['retention_hours'],seconds=300)
  expired=[]
  for name,p in registered.items():
   if p['upper_bound'] is not None and p['upper_bound']<=cutoff:
    # Empty bootstrap/daily partitions are also handled by their proven time bound.
    async with c.transaction():
     await c.execute(f'DROP TABLE {ident(name)}')
     await c.execute('DELETE FROM routing_diagnostic_partitions WHERE name=$1',name)
    expired.append(name)
  state['expired_last']=expired
  until=datetime.datetime.fromisoformat(state['bootstrap_until'])
  start=max(day(now-datetime.timedelta(hours=policy['retention_hours'],seconds=300)),until)
  for offset in range((day(now)+datetime.timedelta(days=4)-start).days):
   lower=start+datetime.timedelta(days=offset);upper=lower+datetime.timedelta(days=1);name='rde14_d'+lower.strftime('%Y%m%d')
   if not await c.fetchval('SELECT to_regclass($1)','public.'+name):
    async with c.transaction():
     await c.execute(f"CREATE TABLE {ident(name)} PARTITION OF {ident(PARENT)} FOR VALUES FROM ('{stamp(lower)}') TO ('{stamp(upper)}')")
     await c.execute("INSERT INTO routing_diagnostic_partitions(name,upper_bound,kind) VALUES($1,$2,'daily')",name,upper)
  if not await c.fetchval("SELECT to_regclass('public.rde14_late')"):
   async with c.transaction():
    await c.execute(f'CREATE TABLE rde14_late PARTITION OF {ident(PARENT)} DEFAULT')
    await c.execute("INSERT INTO routing_diagnostic_partitions(name,kind) VALUES('rde14_late','default')")
  # Late records routed outside active ranges are removed in bounded batches after grace.
  await c.execute('DELETE FROM rde14_late WHERE ctid IN (SELECT ctid FROM rde14_late WHERE event_at<$1 LIMIT 10000)',cutoff)
  await c.execute("UPDATE routing_diagnostic_policy SET retention_checked_at=now(),retention_error=NULL WHERE id")
  state.update(maintained_at=stamp(now),partitions=[r['name'] for r in await c.fetch('SELECT name FROM routing_diagnostic_partitions ORDER BY name')]);save(state);print(json.dumps(state,default=str))
 except BaseException as exc:
  if args.action in ['maintain','cutover']:
   try:await c.execute('UPDATE routing_diagnostic_policy SET retention_error=$1 WHERE id',type(exc).__name__)
   except Exception:pass
  raise
 finally:await c.close()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','cutover','maintain','rollback']);p.add_argument('--database',required=True);p.add_argument('--qa-now');asyncio.run(main(p.parse_args()))
