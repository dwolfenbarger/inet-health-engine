"""Start the same bounded public measurement mesh on the production collector."""
import socket,subprocess
from pathlib import Path
if socket.gethostname()!='inet-health-prod':raise SystemExit('Production collector only')
Path('/etc/systemd/system/inet-atlas-mesh.service').write_text('''[Unit]
Description=INET existing public Atlas anchor mesh
After=network-online.target postgresql.service
[Service]
User=david
Group=david
WorkingDirectory=/home/david/inet-health-engine
EnvironmentFile=/home/david/inet-health-engine/.env
Environment=PYTHONPATH=/opt/inet-routing-canary/collectors
ExecStart=/home/david/inet-health-engine/.venv/bin/python -u -m collectors.atlas_mesh --config /etc/inet-health/atlas-mesh.json
Restart=on-failure
RestartSec=30
TimeoutStopSec=infinity
MemoryMax=512M
CPUQuota=100%
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
[Install]
WantedBy=multi-user.target
''')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','inet-atlas-mesh'],check=True)
