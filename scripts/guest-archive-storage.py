"""Private guest-owned NAS archive guard; uses the existing global CIFS bind."""
import fcntl,json,os,subprocess
from contextlib import contextmanager
from pathlib import Path
ROOT=Path('/var/lib/inet-archive-private/nas')
GIB=1024**3
CAP=350*GIB;ADMIT=260*GIB;DETAIL=120*GIB
STORAGE_ID='e6a0939db1dd4a12a5dbec00ccf45296'
def validate_mount(row,marker):
    if row.get('target')!=str(ROOT) or row.get('fstype')!='cifs':
        raise RuntimeError('NAS bind absent; no local fallback')
    if row.get('source') not in {'//192.168.1.60/nas1','//192.168.1.60/nas1[/storage/ai/ihealth_storage-recovery-20261101]'}:
        raise RuntimeError('NAS source identity differs')
    if marker.get('storage_id')!=STORAGE_ID or marker.get('full_raw_access')!='development-only':
        raise RuntimeError('Archive namespace identity differs')
def mount_check():
    r=subprocess.run(['findmnt','-J','-T',str(ROOT),'-o','TARGET,SOURCE,FSTYPE'],check=True,capture_output=True,text=True,timeout=10)
    row=json.loads(r.stdout)['filesystems'][0]
    # Validate mount before trusting a marker in a possible local fallback.
    if row.get('target')!=str(ROOT) or row.get('fstype')!='cifs':
        raise RuntimeError('NAS bind absent; no local fallback')
    validate_mount(row,json.loads((ROOT/'storage.json').read_text()))
    st=os.statvfs(ROOT)
    if st.f_bavail*st.f_frsize<90*GIB:raise RuntimeError('Shared NAS free-space reserve')
def usage():
    total=detail=files=0
    for parent,dirs,names in os.walk(ROOT,followlinks=False):
        for name in dirs+names:
            if (Path(parent)/name).is_symlink():raise RuntimeError('Symlink in quota namespace')
        for name in names:
            path=Path(parent)/name;size=path.stat().st_size;total+=size;files+=1
            relative=path.relative_to(ROOT).parts
            if 'profiles' in relative or 'journals' in relative:detail+=size
            if files>100000:raise RuntimeError('Catalogue file-count budget')
    return {'total_bytes':total,'detail_bytes':detail,'files':files}
def admit(incoming):
    if incoming<0 or incoming>GIB:raise ValueError('Per-publication compressed budget')
    current=usage()
    if current['total_bytes']+incoming>ADMIT or current['detail_bytes']+incoming>DETAIL:
        raise RuntimeError('Self-imposed NAS quota admission refused')
    return current
@contextmanager
def quota():
    mount_check()
    # A shared CIFS lock serializes production and UAT publishers across hosts.
    with (ROOT/'.archive.lock').open('a+') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        mount_check();admit(0)
        yield
        mount_check()
