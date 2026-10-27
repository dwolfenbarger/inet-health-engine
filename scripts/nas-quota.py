"""One application quota and lock for all managed Internet-health NAS writes."""
import fcntl,json,os,socket,subprocess
from contextlib import contextmanager
from pathlib import Path
ROOT=Path('/Volumes/NAS1/storage/ai/ihealth_storage');GIB=1024**3
CAP=350*GIB;ADMIT=260*GIB;DETAIL=120*GIB
STORAGE_ID='e6a0939db1dd4a12a5dbec00ccf45296'
def mount_check():
    if socket.gethostname() not in {'proxmox1','pve2'}:raise RuntimeError('Archive host only')
    p=subprocess.run(['findmnt','-J','-T',str(ROOT),'-o','TARGET,SOURCE,FSTYPE'],check=True,capture_output=True,text=True,timeout=10)
    f=json.loads(p.stdout)['filesystems'][0]
    if (f['target'],f['source'],f['fstype'])!=('/Volumes/NAS1','//192.168.1.60/nas1','cifs'):raise RuntimeError('NAS identity absent; no local fallback')
    marker=json.loads((ROOT/'storage.json').read_text())
    if marker['storage_id']!=STORAGE_ID or marker['full_raw_access']!='development-only':raise RuntimeError('Storage namespace identity')
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
    if current['total_bytes']+incoming>ADMIT or current['detail_bytes']+incoming>DETAIL:raise RuntimeError('Self-imposed NAS quota admission refused')
    return current
@contextmanager
def quota():
    mount_check()
    # Lock is host-local and covers this project's managed publishers/expiry.
    lock=Path('/var/lock/inet-health-nas.lock')
    with lock.open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);mount_check();admit(0)
        yield
        mount_check()
