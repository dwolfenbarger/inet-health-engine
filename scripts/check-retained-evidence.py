"""Development qualification status without exposing database credentials."""
import asyncio,importlib.util,json,shutil
from pathlib import Path
spec=importlib.util.spec_from_file_location('profile',Path(__file__).with_name('profile-retention.py'))
profile=importlib.util.module_from_spec(spec);spec.loader.exec_module(profile)
async def main():
 profile.setup()
 import asyncpg
 conn=await asyncpg.connect(profile.os.environ['TIMESCALE_DSN'],command_timeout=8)
 try:
  policy=dict(await conn.fetchrow('SELECT * FROM inet_retention.policy WHERE id'))
  rows=await conn.fetch('SELECT source,kind,count(*) AS versions,min(observed_at) AS first_observation,max(observed_at) AS last_observation,max(captured_at) AS last_capture FROM inet_retention.evidence GROUP BY source,kind ORDER BY source,kind')
  gaps=await conn.fetch('SELECT * FROM inet_retention.gaps ORDER BY source,reason')
  summary=await conn.fetch('SELECT source,grain,count(*) AS buckets FROM inet_retention.summaries GROUP BY source,grain ORDER BY source,grain')
  sources=await conn.fetch('SELECT source,state,records,error_code,last_attempt FROM source_status ORDER BY source')
  size=await conn.fetchval("SELECT sum(pg_total_relation_size(oid)) FROM pg_class WHERE relnamespace='inet_retention'::regnamespace AND relkind='r'")
  print(json.dumps({'policy':policy,'evidence':[dict(row) for row in rows],'gaps':[dict(row) for row in gaps],
   'source_status':[dict(row) for row in sources],'summaries':[dict(row) for row in summary],'retention_relation_bytes':size,'root_free_bytes':shutil.disk_usage('/').free},default=str),flush=True)
 finally:await conn.close()
if __name__=='__main__':asyncio.run(main())
