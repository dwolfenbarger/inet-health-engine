"""Proxmox-only, scoped NAS pilot -> existing PBS -> full restored-byte proof.

Uses existing protected PBS credentials internally; never emits credentials or
client command output. Does not enable fleet jobs, prune snapshots, or enable expiry.
"""
import hashlib,importlib.util,json,os,shutil,subprocess,sys,tempfile,time
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('nas_pilot',HERE/'retention-nas-pilot.py')
nas=importlib.util.module_from_spec(spec);spec.loader.exec_module(nas)
def inventory(root):
 result={};total=0
 for path in sorted(root.rglob('*')):
  if path.is_symlink():raise RuntimeError('Unexpected symlink')
  if path.is_file():
   relative=str(path.relative_to(root))
   if any(part.startswith('.pending') for part in Path(relative).parts):raise RuntimeError('Publication in progress')
   total+=path.stat().st_size
   if total>1024**3 or len(result)>=10000:raise RuntimeError('Backup qualification bound')
   h=hashlib.sha256()
   with path.open('rb') as source:
    for chunk in iter(lambda:source.read(1024**2),b''):h.update(chunk)
   result[relative]=h.hexdigest()
 return result,total

def main():
 nas.mount_check();nas.size_guard()
 if shutil.disk_usage('/var/tmp').free<2*1024**3:raise RuntimeError('Restore reserve')
 # Existing storage configuration provides identity/fingerprint, not credential text.
 config={};active=False
 for line in Path('/etc/pve/storage.cfg').read_text().splitlines():
  if line and not line[0].isspace():active=line.strip()=='pbs: pbs_webster'
  elif active and line.strip():
   key,value=line.strip().split(None,1);config[key]=value
 if config.get('server')!='192.168.1.229' or config.get('datastore')!='webster' or not config.get('fingerprint'):
  raise RuntimeError('Independent backup identity differs')
 repository=config['username']+'@'+config['server']+':'+config['datastore']
 env=os.environ.copy();env['PBS_PASSWORD']=Path('/etc/pve/priv/storage/pbs_webster.pw').read_text().strip()
 env['PBS_FINGERPRINT']=config['fingerprint'];env['PBS_LOG']='warn'
 common=['--repository',repository,'--rate','20MiB']
 if config.get('namespace'):common+=['--ns',config['namespace']]
 key=Path('/etc/pve/priv/storage/pbs_webster.enc')
 crypt=['--keyfile',str(key)] if key.exists() else ['--crypt-mode','none']
 def client(args):
  p=subprocess.run(['proxmox-backup-client',*args,*common,*crypt],env=env,capture_output=True,text=True,timeout=600)
  if p.returncode:raise RuntimeError('PBS scoped operation failed (client output withheld); exit='+str(p.returncode))
 before,total=inventory(nas.ROOT);stamp=int(time.time())
 moment=datetime.fromtimestamp(stamp,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
 snapshot='host/inet-health-development/'+moment
 client(['backup','inet-health-development.pxar:'+str(nas.ROOT),'--backup-type','host','--backup-id','inet-health-development','--backup-time',str(stamp)])
 print('SCOPED_PBS_BACKUP_COMPLETE',snapshot,flush=True)
 with tempfile.TemporaryDirectory(prefix='inet-health-restore-',dir='/var/tmp') as temp:
  target=Path(temp)/'restored'
  client(['restore',snapshot,'inet-health-development.pxar',str(target)])
  after,restored=inventory(target)
  if after!=before or restored!=total:raise RuntimeError('Restored file inventory differs')
  sys.path.insert(0,str(HERE.parent/'collectors'/'collectors'))
  from archive_profile import verify
  proofs=[verify(path.parent) for path in sorted((target/'development/uat554/profiles').glob('*/manifest.json'))]
  if len(proofs)<2:raise RuntimeError('Expected development datasets missing')
 report={'schema':'inet-backup-readback-v1','snapshot':snapshot,'backup_server':config['server'],
  'datastore':config['datastore'],'verified_at':datetime.now(timezone.utc).isoformat(),'files':len(before),'bytes':total,
  'profiles_verified':len(proofs),'restored_record_counts':[p['records'] for p in proofs],
  'point_in_time_verified':True,'recurring_coverage_verified':False,'expiry_eligible':False,
  'note':'One scoped independent copy/restore. Ongoing backup coverage, retention policy and quota remain unverified.'}
 nas.mount_check()
 from archive_profile import atomic_json
 atomic_json(nas.ROOT/'backup-readback.json',report)
 print('INDEPENDENT_BACKUP_READBACK',json.dumps(report),flush=True)
if __name__=='__main__':main()
