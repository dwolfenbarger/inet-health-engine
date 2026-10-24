"""Proxmox host-only NAS preflight and bounded development archive publication."""
import argparse,hashlib,json,os,re,socket,subprocess,sys,tempfile,uuid
from datetime import datetime,timezone
from pathlib import Path,PurePosixPath

ROOT=Path('/Volumes/NAS1/storage/ai/ihealth_storage')
BASE=PurePosixPath('/var/lib/inet-health/retention-profile/runs')
SOURCE='//192.168.1.60/nas1';GIB=1024**3

def run(*args,timeout=30):return subprocess.run(args,check=True,text=True,capture_output=True,timeout=timeout).stdout.strip()
def mount_check():
    if socket.gethostname() not in {'proxmox1','pve2'}:raise RuntimeError('Proxmox host only')
    fs=json.loads(run('findmnt','-J','-T',str(ROOT.parent),'-o','TARGET,SOURCE,FSTYPE'))['filesystems'][0]
    if fs['source']!=SOURCE or fs['fstype']!='cifs' or fs['target']!='/Volumes/NAS1':
        raise RuntimeError('Expected NAS filesystem absent; refusing local fallback')
    if os.statvfs(ROOT.parent).f_bavail*os.statvfs(ROOT.parent).f_frsize<90*GIB:
        raise RuntimeError('NAS reserve unavailable')
    return fs

def size_guard():
    total=count=0
    for parent,dirs,files in os.walk(ROOT,followlinks=False):
        for name in dirs+files:
            p=Path(parent)/name
            if p.is_symlink():raise RuntimeError('Symlink in pilot namespace')
        for name in files:
            total+=(Path(parent)/name).stat().st_size;count+=1
            if total>5*GIB or count>10000:raise RuntimeError('Five-GiB development-pilot budget exceeded')
    return total

def prepare():
    fs=mount_check();ROOT.mkdir(exist_ok=True)
    marker=ROOT/'storage.json'
    if marker.exists():
        identity=json.loads(marker.read_text())
        if identity.get('logical_path')!='/NAS1/storage/ai/ihealth_storage' or identity.get('full_raw_access')!='development-only':
            raise RuntimeError('Existing storage identity differs')
    else:
        identity={'schema':'inet-storage-pilot-v1','storage_id':uuid.uuid4().hex,
            'logical_path':'/NAS1/storage/ai/ihealth_storage','host_path':str(ROOT),'source':SOURCE,
            'full_raw_access':'development-only','budget_bytes':350*GIB,'server_quota_verified':False,
            'backup_coverage_verified':False,'expiry_enabled':False,'created_at':datetime.now(timezone.utc).isoformat()}
        with marker.open('x') as output:json.dump(identity,output,sort_keys=True);output.flush();os.fsync(output.fileno())
    probe=ROOT/('.probe-'+uuid.uuid4().hex)
    data=os.urandom(128*1024)
    try:
        with probe.open('xb') as output:output.write(data);output.flush();os.fsync(output.fileno())
        if probe.read_bytes()!=data:raise RuntimeError('NAS readback failed')
    finally:
        if probe.exists():probe.unlink()
    (ROOT/'development'/'uat554'/'profiles').mkdir(parents=True,exist_ok=True)
    print('NAS_PREFLIGHT',json.dumps({'filesystem':fs,'identity':identity,'pilot_bytes':size_guard(),'write_readback_verified':True}),flush=True)
    return identity

def copy_profile(source):
    identity=prepare();path=PurePosixPath(source)
    if path.parent!=BASE or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}',path.name):raise RuntimeError('Unapproved source run')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'collectors'/'collectors'))
    from archive_profile import verify
    target=ROOT/'development'/'uat554'/'profiles'/path.name
    if target.exists():
        print('NAS_PROFILE_ALREADY_VERIFIED',json.dumps(verify(target)),flush=True);return
    stage=target.with_name('.pending-'+path.name);stage.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='inet-retention-') as tmp:
        manifest_path=Path(tmp)/'manifest.json'
        run('pct','pull','554',str(path/'manifest.json'),str(manifest_path))
        manifest=json.loads(manifest_path.read_text())
        if manifest.get('environment')!='development' or not manifest.get('complete') or manifest.get('expiry_eligible') is not False:
            raise RuntimeError('Unqualified development profile')
        if manifest['stored_bytes']>GIB or size_guard()+manifest['stored_bytes']>5*GIB:raise RuntimeError('Pilot size budget')
        if len(manifest['objects'])>512:raise RuntimeError('Manifest object-count budget')
        for item in manifest['objects']:
            name=item['file']
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,60}-[a-f0-9]{64}\.jsonl\.gz',name):raise RuntimeError('Unsafe profile object')
            mount_check();destination=stage/name
            if not destination.exists():run('pct','pull','554',str(path/name),str(destination),timeout=90)
        mount_check()
        with (stage/'manifest.json').open('wb') as output:
            output.write(manifest_path.read_bytes());output.flush();os.fsync(output.fileno())
    proof=verify(stage)
    publication={'storage_id':identity['storage_id'],'published_at':datetime.now(timezone.utc).isoformat(),
        'source_run':str(path),'development_only':True,'expiry_eligible':False,'backup_coverage_verified':False,'proof':proof}
    with (stage/'publication.json').open('w') as output:json.dump(publication,output,sort_keys=True);output.flush();os.fsync(output.fileno())
    mount_check();stage.rename(target)
    print('NAS_PROFILE_PUBLISHED',json.dumps({'path':str(target),**proof}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--copy');args=p.parse_args()
    if args.copy:copy_profile(args.copy)
    else:prepare()
