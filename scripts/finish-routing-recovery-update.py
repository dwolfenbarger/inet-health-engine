"""UAT-only graceful, bounded handoff to reviewed recovery files; no forced stop."""
import hashlib,os,shutil,socket,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
if socket.gethostname()!='inet-health-uat':raise SystemExit('UAT only')
source=Path('/tmp/inet-routing-candidate/collectors/collectors')
target=Path('/opt/inet-routing-canary/collectors/collectors')
expected={'routing_mrt.py':'ecd8e0d7fc77d458e1006aa525415738f93be80ff9392f6855a863683831c22d','routing_store.py':'988ec7eb23f34d4cf3ff72b4edbaf45e821bcb4f35825344cde5881a8ce39fa8'}
def run(*args):return subprocess.run(args,check=True,text=True,capture_output=True).stdout.strip()
def verify():
 for name,digest in expected.items():
  if hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:raise SystemExit('Source hash mismatch: '+name)
verify();deadline=time.monotonic()+2700
while run('systemctl','show','inet-routing-recovery','--property=ActiveState','--value') not in ['inactive','failed']:
 if time.monotonic()>deadline:raise SystemExit('Graceful stop still pending; existing process was not forced')
 time.sleep(5)
verify()
backup=Path('/srv/inet-health-backups')/('routing-recovery-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir()
for name in expected:
 shutil.copy2(target/name,backup/name)
 temporary=target/(name+'.next');shutil.copy2(source/name,temporary)
 previous=(target/name).stat();os.chown(temporary,previous.st_uid,previous.st_gid);temporary.replace(target/name)
run('systemctl','start','inet-routing-recovery')
print('RECOVERY_HANDOFF_COMPLETE',backup,expected,flush=True)
