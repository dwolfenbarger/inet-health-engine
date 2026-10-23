"""Install finite read-only UAT study and first-cause fix; no receiver restart."""
import hashlib,shutil,socket,subprocess
from datetime import datetime,timezone
from pathlib import Path
if socket.gethostname()!='inet-health-uat':raise SystemExit('UAT only')
SOURCE=Path('/tmp/inet-retention-candidate');TARGET=Path('/opt/inet-retention-pilot')
def run(*args):return subprocess.run(args,check=True,text=True,capture_output=True).stdout.strip()
if run('systemctl','show','inet-routing-stream','-p','ActiveState','--value') not in {'inactive','failed'}:
    raise SystemExit('Receiver must already be stopped; will not force it')
stream=Path('/opt/inet-routing-canary/collectors/collectors/routing_stream.py')
expected='e3b9706f8b7a23daf81cb65cb0f0b4f73e1c42744f566d4fef5defb92b1f69a0'
new=(SOURCE/'collectors/collectors/routing_stream.py').read_bytes()
if stream.read_bytes()!=new:
    if hashlib.sha256(stream.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=expected:
        raise SystemExit('Existing receiver differs from reviewed baseline')
    backup=Path('/srv/inet-health-backups')/('retention-stop-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir();shutil.copy2(stream,backup/'routing_stream.py')
    helper=stream.with_name('routing_stop.py')
    if helper.exists():shutil.copy2(helper,backup/'routing_stop.py')
    shutil.copy2(SOURCE/'collectors/collectors/routing_stop.py',helper)
    shutil.copy2(SOURCE/'collectors/collectors/routing_stream.py',stream)
    print('STOP_REASON_FIX_INSTALLED',backup,flush=True)
shutil.copy2(SOURCE/'scripts/profile-retention.py',TARGET/'scripts/profile-retention.py')
Path('/etc/systemd/system/inet-retention-study.service').write_text('''[Unit]
Description=Finite 72-hour Internet health development storage study
After=postgresql.service
[Service]
Type=simple
User=david
Group=david
WorkingDirectory=/home/david/inet-health-engine
ExecStart=/home/david/inet-health-engine/.venv/bin/python /opt/inet-retention-pilot/scripts/profile-retention.py study --source /opt/inet-retention-pilot
RuntimeMaxSec=263000
TimeoutStopSec=30
MemoryMax=128M
CPUQuota=10%
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/inet-health/retention-profile
''')
shutil.copy2(SOURCE/'scripts/retention-capacity-guard.py',TARGET/'scripts/retention-capacity-guard.py')
Path('/var/lib/inet-health-retention-guard').mkdir(mode=0o700,exist_ok=True)
Path('/etc/systemd/system/inet-retention-capacity-guard.service').write_text("""[Unit]
Description=Finite UAT capacity safety pause during retention profiling
After=postgresql.service
[Service]
User=root
ExecStart=/usr/bin/python3 /opt/inet-retention-pilot/scripts/retention-capacity-guard.py
RuntimeMaxSec=263000
TimeoutStopSec=30
MemoryMax=64M
CPUQuota=5%
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/inet-health-retention-guard
""")
run('systemctl','daemon-reload');run('systemctl','start','inet-retention-study');run('systemctl','start','inet-retention-capacity-guard')
print('READ_ONLY_STUDY_STARTED_NOT_BOOT_ENABLED',flush=True)
