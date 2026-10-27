"""Install the requested application quota and host-mediated rotation; no backup work."""
import importlib.util,json,os,subprocess,tempfile
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('nas',HERE/'nas-quota.py');nas=importlib.util.module_from_spec(spec);spec.loader.exec_module(nas)
def run(*args):return subprocess.run(args,check=True,text=True,capture_output=True,timeout=45).stdout.strip()
with nas.quota():
    policy={'schema':'inet-managed-quota-v1','storage_id':nas.STORAGE_ID,'hard_bytes':nas.CAP,
      'admission_bytes':nas.ADMIT,'reserve_bytes':90*nas.GIB,'detail_bytes':nas.DETAIL,
      'enforcement':'Application admission under a host-local exclusive lock; no server-side filesystem quota claimed.',
      'backup_gate':False,'backup_work':'held by user','full_archive_access':'development-only',
      'local_detail_hours':24,'nas_detail_hours':72,'configured_at':datetime.now(timezone.utc).isoformat()}
    temporary=nas.ROOT/'.quota.pending'
    with temporary.open('w') as out:json.dump(policy,out,sort_keys=True);out.flush();os.fsync(out.fileno())
    temporary.replace(nas.ROOT/'quota.json')
    probe=nas.ROOT/'.journal-probe';content=os.urandom(65536)
    try:
        with probe.open('wb') as out:out.write(content);out.flush();os.fsync(out.fileno())
        if probe.read_bytes()!=content:raise RuntimeError('NAS readback probe')
    finally:probe.unlink(missing_ok=True)
    print('APPLICATION_QUOTA',json.dumps({**policy,'used':nas.usage()}),flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        marker=Path(tmp)/'journal-nas-ready.json';marker.write_text(json.dumps(policy))
        run('pct','push','554',str(marker),'/etc/inet-health/journal-nas-ready.json')
    db=run('pct','exec','554','--','/home/david/inet-health-engine/.venv/bin/python',
       '/tmp/inet-operations/scripts/install-journal-runtime.py','--database-only')
    folder=Path('/etc/inet-health-journal');folder.mkdir(exist_ok=True);(folder/'database').write_text(db+'\n')
    Path('/etc/systemd/system/inet-journal-rotation.service').write_text('''[Unit]
Description=INET development journal NAS rotation under managed quota
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/inet-health-journal/scripts/rotate-journal.py
TimeoutStartSec=2700
TimeoutStopSec=30
MemoryMax=512M
CPUQuota=100%
Nice=10
NoNewPrivileges=true
''')
    Path('/etc/systemd/system/inet-journal-rotation.timer').write_text('''[Unit]
Description=Maintain INET development journal every five minutes
[Timer]
OnBootSec=2min
OnUnitInactiveSec=5min
AccuracySec=30s
Unit=inet-journal-rotation.service
[Install]
WantedBy=timers.target
''')
    run('systemctl','daemon-reload');run('systemctl','enable','inet-journal-rotation.timer')
    print('ROTATION_TIMER_INSTALLED_NOT_STARTED',flush=True)
