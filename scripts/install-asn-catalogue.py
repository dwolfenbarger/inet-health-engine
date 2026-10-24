"""Install the bounded derived-catalogue refresh after native release qualification."""
from pathlib import Path
import socket,subprocess
if socket.gethostname() not in {'inet-health-uat','inet-health-prod'}:raise SystemExit('Unexpected host')
root=Path('/home/david/inet-health-engine')
for p in [root/'scripts/build-asn-catalogue.py',root/'docker/migrations/20261024-asn-catalogue.sql',Path('/opt/inet-journal-runtime/vendor/asyncpg')]:
 if not p.exists():raise SystemExit('Missing prerequisite: '+str(p))
subprocess.run(['runuser','-u','postgres','--','psql','-v','ON_ERROR_STOP=1','-d','inethealth','-f',str(root/'docker/migrations/20261024-asn-catalogue.sql')],check=True)
Path('/etc/systemd/system/inet-asn-catalogue.service').write_text('''[Unit]
Description=Publish bounded maintained ASN investigation catalogue
After=postgresql.service
[Service]
Type=oneshot
User=postgres
ExecStart=/usr/bin/python3 -u /home/david/inet-health-engine/scripts/build-asn-catalogue.py
Nice=10
IOSchedulingClass=idle
MemoryMax=512M
TimeoutStartSec=2400
''')
Path('/etc/systemd/system/inet-asn-catalogue.timer').write_text('''[Unit]
Description=Refresh ASN browsing catalogue after previous build completes
[Timer]
OnBootSec=5min
OnUnitInactiveSec=15min
AccuracySec=30s
Persistent=true
[Install]
WantedBy=timers.target
''')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','inet-asn-catalogue.timer'],check=True)
print('Installed catalogue refresh timer; SQL budgets apply to PostgreSQL separately from the Python unit.')
