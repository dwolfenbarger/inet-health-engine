"""Install and resume the qualified collector journal path. Does not manage backups."""
import hashlib,json,os,re,shutil,socket,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
if socket.gethostname() not in {'inet-health-uat','inet-health-prod'}:raise SystemExit('Known collector hosts only')
ROOT=Path('/home/david/inet-health-engine');SOURCE=Path(__file__).resolve().parents[1]
TARGET=Path('/opt/inet-journal-runtime');CANARY=Path('/opt/inet-routing-canary/collectors/collectors')
def run(args,timeout=600):
 result=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
 if result.returncode:
  print('STEP_FAILED',result.stderr[-3000:],flush=True)
  result.check_returncode()
 return result.stdout.strip()
for line in (ROOT/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
u=urlsplit(os.environ['TIMESCALE_DSN']);db=u.path.removeprefix('/')
if u.hostname not in {'localhost','127.0.0.1','::1'} or not re.fullmatch('[a-z][a-z0-9_]{0,62}',db):raise SystemExit('Local database only')
if '--database-only' in sys.argv:
 print(db);raise SystemExit(0)
if run(['systemctl','show','inet-routing-stream','-p','ActiveState','--value']) not in {'inactive','failed'}:raise SystemExit('Receiver must be stopped at a committed boundary')
if shutil.disk_usage('/').free<30*1024**3:raise SystemExit('Root reserve')
# Fail before changing storage if the separately pinned native MRT parser is absent.
run([str(ROOT/'.venv/bin/python'),'-c','import bgpkit; import pybgpkit_parser'])
marker=json.loads(Path('/etc/inet-health/journal-nas-ready.json').read_text())
if marker.get('storage_id')!='e6a0939db1dd4a12a5dbec00ccf45296' or marker.get('admission_bytes')!=260*1024**3:raise SystemExit('NAS quota admission is not ready')
if not CANARY.exists():
 CANARY.parent.parent.mkdir(parents=True,exist_ok=True)
 shutil.copytree(SOURCE/'collectors',CANARY.parent,ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
backup=Path('/srv/inet-health-backups')/('journal-rotation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir()
for name in ['routing_stream.py','routing_store.py']:
 shutil.copy2(CANARY/name,backup/name)
for name in ['journal_rotation.py','archive_profile.py']:
 destination=TARGET/'collectors/collectors'/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(SOURCE/'collectors/collectors'/name,destination)
(TARGET/'scripts').mkdir(parents=True,exist_ok=True);shutil.copy2(SOURCE/'scripts/journal-maintenance.py',TARGET/'scripts/journal-maintenance.py')
(TARGET/'vendor').mkdir(parents=True,exist_ok=True)
if not (TARGET/'vendor/asyncpg').exists():shutil.copytree(ROOT/'.venv/lib/python3.13/site-packages/asyncpg',TARGET/'vendor/asyncpg')
Path('/var/lib/inet-health/journal-export').mkdir(parents=True,exist_ok=True)
shutil.chown('/var/lib/inet-health/journal-export',user='postgres',group='postgres');os.chmod('/var/lib/inet-health/journal-export',0o700)
print('JOURNAL_SCHEMA',run(['runuser','-u','postgres','--','env','LC_ALL=C','psql','-X','-v','ON_ERROR_STOP=1','-At','--dbname',db,'--file',str(SOURCE/'docker/migrations/20261024-journal-rotation.sql')]),flush=True)
print('JOURNAL_PREPARE',run(['runuser','-u','postgres','--','env','PYTHONPATH='+str(TARGET/'vendor'),'/usr/bin/python3',str(TARGET/'scripts/journal-maintenance.py'),'prepare','--database',db]),flush=True)
for name in ['routing_stream.py','routing_store.py','journal_rotation.py']:
 shutil.copy2(SOURCE/'collectors/collectors'/name,CANARY/name)
for unit_name,module in [('inet-routing-stream','routing_stream'),('inet-routing-recovery','routing_mrt')]:
 destination=Path('/etc/systemd/system')/(unit_name+'.service')
 if not destination.exists():
  extra=' --cache /var/lib/inet-health/routing-cache' if module=='routing_mrt' else ''
  destination.write_text('[Unit]\nDescription=INET selected observer '+module+'\nAfter=network-online.target postgresql.service\n[Service]\nUser=david\nWorkingDirectory=/home/david/inet-health-engine\nEnvironmentFile=/home/david/inet-health-engine/.env\nEnvironment=PYTHONPATH=/opt/inet-routing-canary/collectors\nExecStart=/home/david/inet-health-engine/.venv/bin/python -u -m collectors.'+module+' --config /etc/inet-health/routing-observers.json'+extra+'\nRestart=on-failure\nRestartSec=30\nTimeoutStopSec=infinity\nMemoryMax=2G\nCPUQuota=200%\nNoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=read-only\nReadWritePaths=/var/lib/inet-health/routing-cache\n[Install]\nWantedBy=multi-user.target\n')
Path('/var/lib/inet-health/routing-cache').mkdir(parents=True,exist_ok=True);shutil.chown('/var/lib/inet-health/routing-cache',user='david',group='david')
unit=Path('/etc/systemd/system/inet-routing-stream.service.d');unit.mkdir(parents=True,exist_ok=True)
(unit/'30-rotation.conf').write_text('[Service]\nRestart=always\nRestartSec=30\n')
run(['systemctl','daemon-reload']);run(['systemctl','enable','inet-routing-stream','inet-routing-recovery']);run(['systemctl','start','inet-routing-stream','inet-routing-recovery'])
print('CONTINUOUS_RIS_RESUMED',json.dumps({'backup':str(backup),'database':db,'hot_bytes_limit':8*1024**3,'dedup_bytes_limit':8*1024**3,'root_reserve':20*1024**3}),flush=True)
