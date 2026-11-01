"""Archive this guest's sealed journals locally; no host SSH/pct or credentials."""
import argparse,hashlib,importlib.util,json,os,re,shutil,socket,subprocess,sys,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('nas',HERE/'guest-archive-storage.py')
nas=importlib.util.module_from_spec(spec);spec.loader.exec_module(nas)
sys.path.insert(0,str(HERE.parent/'collectors'))
from collectors.archive_profile import verify,atomic_json
CONFIG=Path('/etc/inet-health-journal/guest-archive.json')
MAINT=HERE/'journal-maintenance.py'
SOURCE_CT=None;SOURCE_NS=None;SOURCE_DB=None
def guest(action,*extra,timeout=850):
    env=dict(os.environ,PYTHONPATH=str(HERE.parent/'vendor'))
    r=subprocess.run([sys.executable,str(MAINT),action,'--database',SOURCE_DB,*extra],
        check=True,capture_output=True,text=True,timeout=timeout,env=env)
    lines=[l.removeprefix('RESULT_JSON ') for l in r.stdout.splitlines() if l.startswith('RESULT_JSON ')]
    if len(lines)!=1:raise RuntimeError('Local journal result unavailable')
    return json.loads(lines[0])
def receipt(path,name):
    nas.mount_check()
    proof=verify(path);m=json.loads((path/'manifest.json').read_text())
    if m.get('segment')!=name or m.get('schema')!='inet-journal-segment-v1':
        raise ValueError('Segment identity differs')
    if m.get('source_host')!=socket.gethostname():raise ValueError('Archive source host differs')
    return {'verified':True,'environment':'development','segment':name,'storage_id':nas.STORAGE_ID,
        'source_ct':SOURCE_CT,'namespace':SOURCE_NS,
        'manifest_sha256':hashlib.sha256((path/'manifest.json').read_bytes()).hexdigest(),
        'observers':m['observers'],'from_at':m['from_at'],'until_at':m['until_at'],
        'local_retirement_eligible':datetime.fromisoformat(m['until_at'])<=datetime.now(timezone.utc)-timedelta(hours=24),
        'verified_at':datetime.now(timezone.utc).isoformat(),'records':proof['records']}
def copy_durable(source,target):
    if source.is_symlink():raise ValueError('Symlink source refused')
    with source.open('rb') as inp,target.open('wb') as out:
        shutil.copyfileobj(inp,out,1024**2);out.flush();os.fsync(out.fileno())
def publish(segment):
    name=segment['name']
    if not re.fullmatch(r'routing_messages_(p[0-9]{10}|bootstrap)',name):raise ValueError('Invalid segment')
    target=nas.ROOT/'development'/SOURCE_NS/'journals'/'v2'/name
    if not target.exists():
        exported=guest('export','--segment',name);source=Path(exported['run'])
        if not re.fullmatch(r'/var/lib/inet-health/journal-export/routing_messages_(p[0-9]{10}|bootstrap)-[a-f0-9]{8}',str(source)):
            raise ValueError('Unexpected local export path')
        manifest=json.loads((source/'manifest.json').read_text());nas.admit(manifest['stored_bytes']+256*1024)
        stage=target.with_name('.pending-'+name);stage.mkdir(parents=True,exist_ok=True)
        for obj in manifest['objects']:
            filename=obj['file']
            if not re.fullmatch(r'routing_messages-[a-f0-9]{64}\.jsonl\.gz',filename):raise ValueError('Unsafe archive object')
            nas.mount_check();copy_durable(source/filename,stage/filename)
        atomic_json(stage/'manifest.json',manifest)
        receipt(stage,name);nas.mount_check();stage.rename(target)
        fd=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    proof=receipt(target,name);manifest=json.loads((target/'manifest.json').read_text())
    catalogue=nas.ROOT/'catalogue/journals'/SOURCE_NS;analysis=nas.ROOT/'analysis/routing-activity'/SOURCE_NS
    catalogue.mkdir(parents=True,exist_ok=True);analysis.mkdir(parents=True,exist_ok=True)
    atomic_json(catalogue/(name+'.json'),proof)
    atomic_json(analysis/(name+'.json'),{'algorithm':'captured-frames-v1','contribution_id':SOURCE_NS+'/'+name,'data':manifest['routing_activity'],'receipt':proof})
    with tempfile.TemporaryDirectory(prefix='inet-journal-receipt-') as tmp:
        local=Path(tmp)/'receipt.json';atomic_json(local,proof)
        guest('ack','--segment',name,'--receipt',str(local))
        if proof['local_retirement_eligible']:
            nas.mount_check()
            result=guest('retire','--segment',name,'--receipt',str(local))
            print('JOURNAL_RETIRED',name,json.dumps(result),flush=True)
    print('JOURNAL_VERIFIED',name,'records='+str(proof['records']),flush=True)
def expire():
    for old in guest('expired'):
        name=old['name']
        if not re.fullmatch(r'routing_messages_(p[0-9]{10}|bootstrap)',name):raise ValueError('Invalid expired segment')
        path=nas.ROOT/'development'/SOURCE_NS/'journals'/'v2'/name
        if not path.exists():continue
        if (nas.ROOT/'pins'/SOURCE_NS/name).exists() or (nas.ROOT/'pins'/name).exists():continue
        proof=receipt(path,name);saved=old['receipt']
        saved=json.loads(saved) if isinstance(saved,str) else saved
        if proof['manifest_sha256']!=saved['manifest_sha256']:raise ValueError('Expiry dependency differs')
        nas.mount_check();shutil.rmtree(path)
        proof.update({'raw_state':'expired_under_72_hour_policy','expired_at':datetime.now(timezone.utc).isoformat()})
        atomic_json(nas.ROOT/'catalogue/journals'/SOURCE_NS/(name+'.json'),proof)
        print('NAS_RAW_EXPIRED',name,flush=True)
def main():
    global SOURCE_CT,SOURCE_NS,SOURCE_DB
    p=argparse.ArgumentParser();p.add_argument('--batches',type=int,default=1);p.add_argument('--check',action='store_true');args=p.parse_args()
    if not 1<=args.batches<=12:raise ValueError('Batch bound')
    cfg=json.loads(CONFIG.read_text());SOURCE_CT=cfg['ct'];SOURCE_NS=cfg['namespace'];SOURCE_DB=cfg['database']
    expected={553:('inet-health-prod','prod553'),554:('inet-health-uat','uat554')}
    if expected.get(SOURCE_CT)!=(socket.gethostname(),SOURCE_NS):raise ValueError('Guest identity differs')
    if not re.fullmatch('[a-z][a-z0-9_]{0,62}',SOURCE_DB):raise ValueError('Invalid database name')
    with nas.quota():
        if args.check:
            print(json.dumps({'mount_verified':True,'usage':guest('status'),'quota':nas.usage()}));return
        for _ in range(args.batches):
            segments=guest('list')
            if not segments:break
            for segment in segments:publish(segment)
            # Existing cleanup bounds are retained; only completed local export staging.
            guest('cleanup')
        expire()
        print('JOURNAL_MAINTENANCE',json.dumps({'usage':guest('status'),'quota':nas.usage()}),flush=True)
if __name__=='__main__':main()
