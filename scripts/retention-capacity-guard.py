"""Finite UAT-only safety pause of future Celery scheduling; never stops workers."""
import argparse,json,shutil,socket,subprocess,time
from datetime import datetime,timedelta,timezone
from pathlib import Path

STATE=Path('/var/lib/inet-health-retention-guard')

def check_once(threshold_gib=30,dry_run=False):
    if socket.gethostname()!='inet-health-uat':raise RuntimeError('UAT only')
    if not 20<=threshold_gib<=45:raise ValueError('Threshold outside reviewed UAT bounds')
    free=shutil.disk_usage('/').free
    result={'at':datetime.now(timezone.utc).isoformat(),'free_bytes':free,'threshold_bytes':threshold_gib*1024**3,
        'dry_run':dry_run,'paused_future_scheduling':False}
    if free<result['threshold_bytes']:
        result['action']='stop inet-celery-beat only; leave workers/API and queues intact'
        if not dry_run:
            subprocess.run(['systemctl','stop','inet-celery-beat'],check=True,timeout=30,capture_output=True)
            result['paused_future_scheduling']=True
    else:result['action']='observe'
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--once',action='store_true');p.add_argument('--dry-run',action='store_true');args=p.parse_args()
    if socket.gethostname()!='inet-health-uat':raise SystemExit('UAT only')
    if args.once:
        print(json.dumps(check_once(dry_run=args.dry_run)),flush=True);return
    STATE.mkdir(mode=0o700,exist_ok=True)
    path=STATE/'study.json'
    if path.is_symlink():raise SystemExit('Symlink state refused')
    if path.exists():state=json.loads(path.read_text())
    else:
        now=datetime.now(timezone.utc);state={'started_at':now.isoformat(),'until':(now+timedelta(hours=72)).isoformat()}
        path.write_text(json.dumps(state))
    while datetime.now(timezone.utc)<datetime.fromisoformat(state['until']):
        result=check_once();print('UAT_RETENTION_CAPACITY_GUARD',json.dumps(result),flush=True)
        if result['paused_future_scheduling']:
            (STATE/'scheduling-paused.json').write_text(json.dumps(result));return
        time.sleep(600)

if __name__=='__main__':main()
