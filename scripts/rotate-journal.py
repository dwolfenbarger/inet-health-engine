"""Host-mediated journal publication, quota admission, readback and retirement.

Independent backup coverage is deliberately not a gate for this requested policy.
No container mount/protection change; no live PostgreSQL files on NAS.
"""
import hashlib,importlib.util,json,re,shutil,subprocess,sys,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('nas',HERE/'nas-quota.py');nas=importlib.util.module_from_spec(spec);spec.loader.exec_module(nas)
sys.path.insert(0,str(HERE.parent/'collectors'))
from collectors.archive_profile import verify,atomic_json
DATABASE_FILE=Path('/etc/inet-health-journal/database')
SOURCE_CT=554;SOURCE_NS='uat554';SOURCE_DB=None
GUEST='/opt/inet-journal-runtime/scripts/journal-maintenance.py'
def run(args,timeout=45):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=timeout).stdout

def guest(action,*extra,timeout=900):
    db=SOURCE_DB or DATABASE_FILE.read_text().strip()
    if not re.fullmatch('[a-z][a-z0-9_]{0,62}',db):raise ValueError('Unexpected database name')
    command=['pct','exec',str(SOURCE_CT),'--','systemd-run','--quiet','--wait','--pipe','--collect',
      '--unit','inet-journal-step-'+datetime.now(timezone.utc).strftime('%H%M%S%f'),
      '--property=User=postgres','--property=MemoryMax=512M','--property=CPUQuota=100%',
      '--property=RuntimeMaxSec=850','--setenv=PYTHONPATH=/opt/inet-journal-runtime/vendor',
      '/usr/bin/python3',GUEST,action,'--database',db,*extra]
    result=run(command,timeout)
    lines=[line.removeprefix('RESULT_JSON ') for line in result.splitlines() if line.startswith('RESULT_JSON ')]
    if len(lines)!=1:raise RuntimeError('Guest result unavailable')
    return json.loads(lines[0])

def receipt(path,segment):
    proof=verify(path);manifest=json.loads((path/'manifest.json').read_text())
    if manifest.get('segment')!=segment or manifest.get('schema')!='inet-journal-segment-v1':raise ValueError('Operational segment identity differs')
    return {'verified':True,'environment':'development','segment':segment,'storage_id':nas.STORAGE_ID,
      'source_ct':SOURCE_CT,'namespace':SOURCE_NS,'manifest_sha256':hashlib.sha256((path/'manifest.json').read_bytes()).hexdigest(),
      'observers':manifest['observers'],'from_at':manifest['from_at'],'until_at':manifest['until_at'],
      'local_retirement_eligible':datetime.fromisoformat(manifest['until_at'])<=datetime.now(timezone.utc)-timedelta(hours=24),
      'verified_at':datetime.now(timezone.utc).isoformat(),'records':proof['records']}

def publish(segment):
    name=segment['name']
    if not re.fullmatch(r'routing_messages_(p[0-9]{10}|bootstrap)',name):raise ValueError('Invalid segment')
    target=nas.ROOT/'development'/SOURCE_NS/'journals'/'v2'/name
    if not target.exists():
        exported=guest('export','--segment',name)
        source=exported['run']
        if not re.fullmatch(r'/var/lib/inet-health/journal-export/routing_messages_(p[0-9]{10}|bootstrap)-[a-f0-9]{8}',source):raise ValueError('Unexpected export path')
        with tempfile.TemporaryDirectory(prefix='inet-journal-manifest-') as tmp:
            local=Path(tmp)/'manifest.json';run(['pct','pull',str(SOURCE_CT),source+'/manifest.json',str(local)])
            manifest=json.loads(local.read_text());nas.admit(manifest['stored_bytes']+256*1024)
            stage=target.with_name('.pending-'+name);stage.mkdir(parents=True,exist_ok=True)
            for obj in manifest['objects']:
                file=obj['file']
                if not re.fullmatch(r'routing_messages-[a-f0-9]{64}\.jsonl\.gz',file):raise ValueError('Unsafe journal object')
                nas.mount_check()
                run(['pct','pull',str(SOURCE_CT),source+'/'+file,str(stage/file)],timeout=120)
            atomic_json(stage/'manifest.json',manifest);receipt(stage,name)
            nas.mount_check();stage.rename(target)
    proof=receipt(target,name)
    manifest=json.loads((target/'manifest.json').read_text())
    catalogue=nas.ROOT/'catalogue/journals'/SOURCE_NS;analysis=nas.ROOT/'analysis/routing-activity'/SOURCE_NS
    catalogue.mkdir(parents=True,exist_ok=True);analysis.mkdir(parents=True,exist_ok=True)
    atomic_json(catalogue/(name+'.json'),proof)
    atomic_json(analysis/(name+'.json'),{'algorithm':'captured-frames-v1','contribution_id':SOURCE_NS+'/'+name,'data':manifest['routing_activity'],'receipt':proof})
    with tempfile.TemporaryDirectory(prefix='inet-journal-receipt-') as temp:
        local=Path(temp)/'receipt.json';atomic_json(local,proof)
        remote='/tmp/inet-journal-receipt-'+name+'.json';run(['pct','push',str(SOURCE_CT),str(local),remote])
        guest('ack','--segment',name,'--receipt',remote)
        if datetime.fromisoformat(segment['until_at'])<=datetime.now(timezone.utc)-timedelta(hours=24):
            result=guest('retire','--segment',name,'--receipt',remote);print('JOURNAL_RETIRED',name,result,flush=True)
    print('JOURNAL_VERIFIED',json.dumps(proof),flush=True)

def maintain_source():
    for segment in guest('list'):publish(segment)
    for old in guest('expired'):
        name=old['name']
        if not re.fullmatch(r'routing_messages_(p[0-9]{10}|bootstrap)',name):raise ValueError('Invalid expired segment')
        path=nas.ROOT/'development'/SOURCE_NS/'journals'/'v2'/name
        if not path.exists():continue
        if (nas.ROOT/'pins'/SOURCE_NS/name).exists() or (nas.ROOT/'pins'/name).exists():continue
        proof=receipt(path,name)
        saved=old['receipt'];saved=json.loads(saved) if isinstance(saved,str) else saved
        if proof['manifest_sha256']!=saved['manifest_sha256']:raise ValueError('Expiry dependency differs')
        nas.mount_check();shutil.rmtree(path)
        proof.update({'raw_state':'expired_under_72_hour_policy','expired_at':datetime.now(timezone.utc).isoformat()})
        atomic_json(nas.ROOT/'catalogue/journals'/SOURCE_NS/(name+'.json'),proof)
        print('NAS_RAW_EXPIRED',name,flush=True)
    print('JOURNAL_MAINTENANCE',json.dumps({'usage':guest('status'),'quota':nas.usage(),'cleanup':guest('cleanup')}),flush=True)
def main():
    global SOURCE_CT,SOURCE_NS,SOURCE_DB
    path=Path('/etc/inet-health-journal/sources.json')
    sources=json.loads(path.read_text()) if path.exists() else [{'ct':554,'namespace':'uat554','database':DATABASE_FILE.read_text().strip()}]
    errors=[]
    with nas.quota():
        for source in sources:
            if (source['ct'],source['namespace']) not in {(553,'prod553'),(554,'uat554')}:raise ValueError('Unapproved collector identity')
            SOURCE_CT=source['ct'];SOURCE_NS=source['namespace'];SOURCE_DB=source['database']
            try:maintain_source()
            except Exception as exc:
                errors.append(SOURCE_CT);print('JOURNAL_SOURCE_FAILED',SOURCE_CT,type(exc).__name__,str(exc)[-400:],flush=True)
    if errors:raise RuntimeError('Journal maintenance failed for '+str(errors))
if __name__=='__main__':main()
