"""Opt-in UAT mesh service and guarded observer configuration update."""
import hashlib,json,os,shutil,socket,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
if socket.gethostname()!='inet-health-uat':raise SystemExit('UAT only')
root=Path('/home/david/inet-health-engine');candidate=Path('/tmp/inet-routing-candidate');isolated=Path('/opt/inet-routing-canary/collectors/collectors')
def run(*args):return subprocess.run(args,check=True,text=True,capture_output=True).stdout.strip()
if shutil.disk_usage('/').free<40*1024**3:raise SystemExit('Need 40 GiB free before expansion')
for line in (root/'.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
if sys.argv[1]=='mesh':
 config=Path('/etc/inet-health/atlas-mesh.json')
 if config.exists():raise SystemExit('Existing mesh configuration; inspect before replacement')
 shutil.copy2(candidate/'atlas-mesh-uat.json',config)
 shutil.copy2(candidate/'collectors/collectors/atlas_mesh.py',isolated/'atlas_mesh.py')
 os.chown(isolated/'atlas_mesh.py',int(run('id','-u','david')),int(run('id','-g','david')))
 Path('/etc/systemd/system/inet-atlas-mesh.service').write_text('''[Unit]
Description=Internet health public Atlas anchor mesh canary
After=network-online.target postgresql.service
[Service]
User=david
Group=david
WorkingDirectory=/home/david/inet-health-engine
EnvironmentFile=/home/david/inet-health-engine/.env
Environment=PYTHONPATH=/opt/inet-routing-canary/collectors
ExecStart=/home/david/inet-health-engine/.venv/bin/python -m collectors.atlas_mesh --config /etc/inet-health/atlas-mesh.json
Restart=on-failure
RestartSec=30
TimeoutStopSec=infinity
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
MemoryMax=512M
CPUQuota=100%
[Install]
WantedBy=multi-user.target
''')
 run('systemctl','daemon-reload');run('systemctl','start','inet-atlas-mesh')
 print('Mesh canary started; not enabled at boot',flush=True)
elif sys.argv[1]=='mesh-refresh':
 if run('systemctl','show','inet-atlas-mesh','--property=ActiveState','--value') not in ['inactive','failed']:
  raise SystemExit('Mesh service has not stopped gracefully')
 config=Path('/etc/inet-health/atlas-mesh.json')
 backup=config.with_name('atlas-mesh-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
 shutil.copy2(config,backup);shutil.copy2(candidate/'atlas-mesh-uat.json',config)
 shutil.copy2(candidate/'collectors/collectors/atlas_mesh.py',isolated/'atlas_mesh.py')
 run('systemctl','start','inet-atlas-mesh');print('Mesh configuration replaced; backup',backup,flush=True)
elif sys.argv[1]=='routing':
 for unit in ['inet-routing-stream','inet-routing-recovery']:
  state=run('systemctl','show',unit,'--property=ActiveState','--value')
  if state not in ['inactive','failed']:raise SystemExit('Routing service has not stopped gracefully: '+unit)
 config=Path('/etc/inet-health/routing-observers.json');new=json.loads((candidate/'observers-uat.json').read_text());old=json.loads(config.read_text())
 if any(o not in new for o in old):raise SystemExit('Expansion must retain all existing observer identities')
 if sum(o['mode']=='ris-live' for o in new)>4 or len(new)>6:raise SystemExit('Observer budget exceeded')
 backup=config.with_name('routing-observers-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json');shutil.copy2(config,backup)
 shutil.copy2(candidate/'observers-uat.json',config)
 shutil.copy2(candidate/'collectors/collectors/routing_stream.py',isolated/'routing_stream.py')
 shutil.copy2(candidate/'collectors/collectors/routing_mrt.py',isolated/'routing_mrt.py')
 run('systemctl','start','inet-routing-stream');run('systemctl','start','inet-routing-recovery')
 print('Expanded to',len(new),'observers; configuration backup',backup,flush=True)
else:raise SystemExit('Use mesh or routing')
