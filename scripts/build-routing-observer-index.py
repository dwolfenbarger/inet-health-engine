"""Add the bounded observer/time access path without stopping ingestion."""
from pathlib import Path
from urllib.parse import urlparse
import socket,subprocess
if socket.gethostname()!='inet-health-uat':raise SystemExit('UAT only')
values={}
for line in Path('/home/david/inet-health-engine/.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);values[k.strip()]=v.strip().strip('"').strip("'")
database=urlparse(values['TIMESCALE_DSN']).path.lstrip('/')
subprocess.run(['runuser','-u','postgres','--','psql','-v','ON_ERROR_STOP=1','-d',database,'-c','CREATE INDEX CONCURRENTLY IF NOT EXISTS routing_routes_observer_time ON routing_routes(observer,event_at)'],check=True)
print('OBSERVER_TIME_INDEX_CREATED',flush=True)
