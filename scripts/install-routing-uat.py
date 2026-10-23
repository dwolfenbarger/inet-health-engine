"""Install additive selected-observer canary services on UAT only."""
import os,subprocess,shutil
from pathlib import Path
assert os.uname().nodename=='inet-health-uat'
root=Path('/home/david/inet-health-engine')
source=Path('/tmp/inet-routing-candidate')
# Isolated code path leaves the native API/collectors on the previous release.
install=Path('/opt/inet-routing-canary');install.mkdir(exist_ok=True)
shutil.copytree(source/'collectors',install/'collectors',dirs_exist_ok=True)
config=Path('/etc/inet-health/routing-observers.json')
if config.exists():raise SystemExit('Existing observer configuration needs review')
config.write_bytes((source/'observers-uat.json').read_bytes());config.chmod(0o644)
cache=Path('/var/lib/inet-health/routing-cache');cache.mkdir(parents=True,exist_ok=True)
subprocess.run(['chown','-R','david:david',str(install),str(cache)],check=True)
for name,module,arguments in [
 ('inet-routing-stream','collectors.routing_stream',''),
 ('inet-routing-recovery','collectors.routing_mrt',' --cache /var/lib/inet-health/routing-cache')]:
 unit=f'''[Unit]
Description=INET selected-observer routing canary ({name})
After=network-online.target postgresql.service
Wants=network-online.target
[Service]
User=david
WorkingDirectory={root}
EnvironmentFile={root}/.env
Environment=PYTHONPATH={install}/collectors
ExecStart={root}/.venv/bin/python -u -m {module} --config {config}{arguments}
Restart=on-failure
RestartSec=30
TimeoutStopSec=infinity
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/inet-health/routing-cache
MemoryMax=2G
CPUQuota=200%
[Install]
WantedBy=multi-user.target
'''
 (Path('/etc/systemd/system')/(name+'.service')).write_text(unit)
subprocess.run(['systemctl','daemon-reload'],check=True)
# Start stream first, so archive bootstrap/replay can overlap live updates.
subprocess.run(['systemctl','start','inet-routing-stream'],check=True)
subprocess.run(['systemctl','start','inet-routing-recovery'],check=True)
print('UAT_ROUTING_CANARY_STARTED',flush=True)
