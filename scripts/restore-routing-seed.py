"""Add a consistent maintained-route seed to production; refuse any existing target."""
import hashlib,json,os,re,socket,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
if socket.gethostname()!='inet-health-prod':raise SystemExit('Production bootstrap target only')
path=Path('/tmp/inet-routing-seed.dump');wanted=sys.argv[1]
if not re.fullmatch('[a-f0-9]{64}',wanted):raise SystemExit('Expected checksum required')
h=hashlib.sha256()
with path.open('rb') as stream:
 for chunk in iter(lambda:stream.read(1024**2),b''):h.update(chunk)
if h.hexdigest()!=wanted:raise SystemExit('Seed checksum differs')
for line in Path('/home/david/inet-health-engine/.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
u=urlsplit(os.environ['TIMESCALE_DSN']);db=u.path.removeprefix('/')
if u.hostname not in {'localhost','127.0.0.1','::1'} or not re.fullmatch('[a-z][a-z0-9_]{0,62}',db):raise SystemExit('Local database required')
def command(args,timeout=1800):
 r=subprocess.run(['runuser','-u','postgres','--','env','LC_ALL=C',*args],check=True,capture_output=True,text=True,timeout=timeout);return r.stdout.strip()
existing=command(['psql','-X','-At','--dbname',db,'--command',"SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename IN ('routing_observers','routing_routes','routing_gaps','routing_archives','routing_message_keys')"])
if existing!='0':raise SystemExit('Target routing tables already exist; will not overwrite')
print('RESTORING_CURRENT_ROUTING_STATE',wanted,flush=True)
print(command(['pg_restore','--single-transaction','--exit-on-error','--no-owner','--no-acl','--dbname',db,str(path)]),flush=True)
command(['psql','-X','-v','ON_ERROR_STOP=1','--dbname',db,'--command','GRANT SELECT,INSERT,UPDATE ON routing_observers,routing_routes,routing_gaps,routing_archives TO inetuser; GRANT SELECT ON routing_message_keys TO inetuser;'])
proof={'source_ct':554,'source':'Consistent development collector current state and dedup identities; no raw journal imported',
 'sha256':wanted,'restored_at':datetime.now(timezone.utc).isoformat(),'historical_gaps_preserved':True}
Path('/etc/inet-health/routing-seed.json').write_text(json.dumps(proof,sort_keys=True))
print('ROUTING_SEED_RESTORED',json.dumps(proof),flush=True)
