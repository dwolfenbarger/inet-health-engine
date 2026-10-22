"""Real PostgreSQL evidence ordering and failure-state regression; roll back all writes."""
import asyncio,os,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
from contextlib import asynccontextmanager
from unittest.mock import patch
root=Path('/home/david/inet-health-engine')
for line in (root/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
sys.path.insert(0,str((Path(sys.argv[1]) if len(sys.argv)>1 else root)/'collectors'))
async def main():
 import asyncpg
 from collectors.source_evidence import save_cycle,source_failure
 conn=await asyncpg.connect(os.environ['TIMESCALE_DSN'])
 class Pool:
  @asynccontextmanager
  async def acquire(self):yield conn
 async def pool():return Pool()
 transaction=conn.transaction();await transaction.start()
 try:
  with patch('collectors.source_evidence.get_pg_pool',pool):
   now=datetime.now(timezone.utc);source='regression-rollback'
   record={'id':'stable-id','observed_at':now,'asn':4288000245,'family':6,'resource':'2001:db8::/32','payload':{'value':'new'}}
   await save_cycle(source,[record],{'scope':'test'})
   await save_cycle(source,[{**record,'observed_at':now-timedelta(minutes=1),'payload':{'value':'old'}}],{'scope':'test'})
   row=await conn.fetchrow('SELECT asn,payload FROM source_evidence WHERE source=$1',source)
   assert row['asn']==4288000245 and 'new' in row['payload'] and 'old' not in row['payload']
   before=await conn.fetchval('SELECT last_success FROM source_status WHERE source=$1',source)
   await source_failure(source,RuntimeError('private credential or URL must not appear'))
   row=await conn.fetchrow('SELECT * FROM source_status WHERE source=$1',source)
   assert row['state']=='error' and row['last_success']==before and row['last_observation']==now
   assert 'private' not in row['detail'] and row['error_code']=='RuntimeError'
  print('SOURCE_STORAGE_PASSED: ordering, high ASN, idempotent identity, failure preserves prior success',flush=True)
 finally:
  await transaction.rollback()
  assert await conn.fetchval("SELECT count(*) FROM source_evidence WHERE source='regression-rollback'")==0
  await conn.close()
 print('All source regression records rolled back',flush=True)
asyncio.run(main())
