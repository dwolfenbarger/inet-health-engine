"""Expose verified NAS development archive read-only to UAT without reboot."""
import json,socket,subprocess
from datetime import datetime,timezone
from pathlib import Path
SOURCE='/Volumes/NAS1/storage/ai/ihealth_storage';DEST='/srv/inet-health/archive'
def run(*args):return subprocess.run(args,check=True,text=True,capture_output=True,timeout=30).stdout.strip()
if socket.gethostname() not in {'proxmox1','pve2'}:raise SystemExit('Proxmox only')
fs=json.loads(run('findmnt','-J','-T',SOURCE,'-o','TARGET,SOURCE,FSTYPE'))['filesystems'][0]
if fs['source']!='//192.168.1.60/nas1' or fs['fstype']!='cifs':raise SystemExit('Verified NAS is absent')
config=run('pct','config','554');line=next((x for x in config.splitlines() if x.startswith('mp0:')),None)
if line:
    if SOURCE not in line or 'ro=1' not in line or 'mp='+DEST not in line:raise SystemExit('mp0 already belongs to another mount')
else:
    if 'protection: 1' in config.splitlines():
        print('MOUNT_DEFERRED_PROTECTION_ENABLED_USE_VERIFIED_HOST_PUBLICATION',flush=True)
        raise SystemExit(0)
    run('pct','exec','554','--','install','-d',DEST)
    backup=Path('/var/lib/inet-health-backups');backup.mkdir(mode=0o700,exist_ok=True)
    (backup/('lxc554-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.conf')).write_text(config+'\n')
    run('pct','set','554','-mp0',SOURCE+',mp='+DEST+',ro=1,backup=0')
print('UAT_READ_ONLY_MOUNT',run('pct','exec','554','--','findmnt','-T',DEST,'-o','TARGET,SOURCE,FSTYPE,OPTIONS'),flush=True)
run('pct','exec','554','--','runuser','-u','david','--','test','-r',DEST+'/storage.json')
result=subprocess.run(['pct','exec','554','--','runuser','-u','david','--','test','-w',DEST+'/storage.json'])
if result.returncode==0:raise SystemExit('Archive unexpectedly writable in UAT')
print('DEVELOPMENT_READBACK_ALLOWED_WRITE_REFUSED_BACKUP_COVERAGE_UNVERIFIED',flush=True)
