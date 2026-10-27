"""Enable the additive bounded development capture, with no native worker restart."""
import hashlib,json,re,shutil,socket,subprocess,sys
from pathlib import Path
from urllib.parse import urlsplit
SOURCE=Path(__file__).resolve().parents[1];TARGET=Path('/opt/inet-retention-pilot')
if socket.gethostname()!='inet-health-uat':raise SystemExit('UAT qualification only')
if shutil.disk_usage('/').free<40*1024**3:raise SystemExit('Insufficient qualification reserve')
sys.path.insert(0,str(SOURCE/'scripts'))
import importlib.util
spec=importlib.util.spec_from_file_location('profile',SOURCE/'scripts/profile-retention.py')
profile=importlib.util.module_from_spec(spec);spec.loader.exec_module(profile);profile.setup()
db=urlsplit(profile.os.environ['TIMESCALE_DSN']).path.removeprefix('/')
if not re.fullmatch('[a-z][a-z0-9_]{0,62}',db):raise SystemExit('Unexpected local database name')
def sql(query=None,path=None):
 command=['runuser','-u','postgres','--','env','LC_ALL=C','psql','-X','-v','ON_ERROR_STOP=1','-At','--dbname',db]
 command+=['--file',str(path)] if path else ['--command',query]
 return subprocess.run(command,check=True,text=True,capture_output=True,timeout=45).stdout.strip()
migration=SOURCE/'docker/migrations/20261024-retained-evidence.sql'
print('ADDITIVE_SCHEMA',sql(path=migration),flush=True)
# Re-running never extends the finite window, resets accounting, or permits expiry.
print('CAPTURE_POLICY',sql("UPDATE inet_retention.policy SET started_at=clock_timestamp(),enabled_until=clock_timestamp()+interval '72 hours' WHERE id AND started_at IS NULL RETURNING started_at,enabled_until,max_bytes,max_records"),flush=True)
# Bounded 28-direction seed only. Historical built-in/latest tables are not a full backfill.
print('MESH_SEED',sql("SET lock_timeout='3s'; SET statement_timeout='30s'; UPDATE source_evidence SET payload=payload WHERE source='atlas-mesh'"),flush=True)
for name in ['retained-summaries.py','profile-retention.py']:
 shutil.copy2(SOURCE/'scripts'/name,TARGET/'scripts'/name)
Path('/etc/systemd/system/inet-retained-summaries.service').write_text('''[Unit]
Description=Finite development observed-cohort summary qualification
After=postgresql.service
[Service]
User=david
Group=david
WorkingDirectory=/home/david/inet-health-engine
ExecStart=/home/david/inet-health-engine/.venv/bin/python /opt/inet-retention-pilot/scripts/retained-summaries.py --study
RuntimeMaxSec=263000
TimeoutStopSec=35
MemoryMax=256M
CPUQuota=20%
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
''')
# The UAT application role can write analysis rows; retained evidence remains SELECT-only.
print('SUMMARY_GRANT',sql('GRANT INSERT,UPDATE ON inet_retention.summaries TO inetuser'),flush=True)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','start','inet-retained-summaries'],check=True)
print('QUALIFICATION_STARTED_NOT_BOOT_ENABLED',json.dumps({'migration_sha256':hashlib.sha256(migration.read_bytes()).hexdigest(),'expiry_enabled':False}),flush=True)
