"""Exercise the scheduled scan against isolated PostgreSQL, including bind failure."""
import asyncio,json,socket,sys,os
from pathlib import Path
from datetime import datetime,timezone
assert socket.gethostname()=='inet-health-dev1','DEV only'
os.chdir(Path(__file__).resolve().parents[1])
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'collectors'))
from collectors.hijack_detector import detect_origin_changes
import asyncpg
DB='inet_diag_qa_20261027'
async def main():
 from urllib.parse import urlsplit
 assert urlsplit(os.environ['TIMESCALE_DSN']).path=='/'+DB
 pool=await asyncpg.create_pool(dsn=os.environ['TIMESCALE_DSN'],min_size=1,max_size=2)
 try:
  async with pool.acquire() as c:
   assert await c.fetchval('SELECT current_database()')==DB
   await c.execute('''CREATE TABLE IF NOT EXISTS bgp_updates(time timestamptz,prefix text,origin_asn bigint);
    CREATE TABLE IF NOT EXISTS prefix_origins(prefix text,origin_asn bigint,last_seen timestamptz,observation_count bigint);
    CREATE TABLE IF NOT EXISTS bgp_anomalies(time timestamptz,event_type text,affected_prefix text,origin_asn bigint,expected_asn bigint,severity integer,confidence float,source text,raw_data jsonb);
    TRUNCATE bgp_updates,prefix_origins,bgp_anomalies;''')
   await c.execute("INSERT INTO bgp_updates VALUES(now(),'198.18.0.0/24',64502)")
   await c.execute("INSERT INTO prefix_origins VALUES('198.18.0.0/24',64501,now(),7)")
  if '--expect-failure' in sys.argv:
   try:await detect_origin_changes(pool)
   except asyncpg.IndeterminateDatatypeError as e:assert 'parameter $5' in str(e)
   else:raise AssertionError('Original fault was not reproduced')
   assert await pool.fetchval('SELECT count(*) FROM bgp_anomalies')==0
   result={'original_failure_reproduced':True,'sqlstate':'42P18','partial_inserts':0}
  else:
   count=await detect_origin_changes(pool);assert count==1
   row=await pool.fetchrow('SELECT * FROM bgp_anomalies');raw=json.loads(row['raw_data'])
   assert row['origin_asn']==64502 and row['expected_asn']==64501 and raw=={'baseline_count':7}
   assert type(raw['baseline_count']) is int
   result={'pass':True,'inserted':count,'numeric_baseline_count':raw['baseline_count'],'source':row['source']}
  print(json.dumps(result))
 finally:await pool.close()
asyncio.run(main())
