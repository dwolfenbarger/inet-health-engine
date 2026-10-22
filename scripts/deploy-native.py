"""Native promotion with a retained rollback and a hard worker-drain gate.

Usage: deploy_native.py drain | install EXPECTED_SHA FRONTEND_SHA BUNDLE_SHA | rollback BACKUP
All subprocess output is bounded; no credentials are printed.
"""
import hashlib, json, os, shutil, socket, subprocess, sys, tarfile
from datetime import datetime, timezone
from pathlib import Path

root=Path('/home/david/inet-health-engine')
if socket.gethostname() not in ['inet-health-uat','inet-health-prod']:
    raise SystemExit('Deployment host not allowed')

def run(args, **kwargs):
    return subprocess.run(args,check=True,text=True,**kwargs)

def git(*args):
    return run(['runuser','-u','david','--','git','-C',str(root),*args],capture_output=True).stdout.strip()

def env():
    for line in (root/'.env').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
    os.environ['PYTHONPATH']=str(root/'collectors')
    sys.path.insert(0,str(root/'collectors'))
    os.chdir('/tmp')

def idle():
    env()
    from collectors.celery_app import app
    inspector=app.control.inspect(timeout=4)
    for name in ['active','reserved','scheduled']:
        result=getattr(inspector,name)()
        if not result:
            if subprocess.run(['systemctl','is-active','--quiet','inet-celery-worker']).returncode==0:
                raise SystemExit('Worker online but inspection unavailable; drain not proven')
        counts={k:len(v) for k,v in (result or {}).items()}
        print(name,json.dumps(counts),flush=True)
        if any(counts.values()):
            raise SystemExit(2)

action=sys.argv[1]
if action=='drain':
    run(['systemctl','stop','inet-celery-beat'])
    idle()
    print('DRAINED: scheduler stopped; no active, reserved or delayed tasks',flush=True)
elif action=='install':
    sha,frontend_sha,bundle_sha=sys.argv[2:5]
    for name,wanted in [('frontend.tar.gz',frontend_sha),('release.bundle',bundle_sha)]:
        if hashlib.sha256(Path('/tmp',name).read_bytes()).hexdigest()!=wanted:
            raise SystemExit('Artifact checksum mismatch: '+name)
    if subprocess.run(['systemctl','is-active','--quiet','inet-celery-beat']).returncode==0:
        raise SystemExit('Stop and drain scheduler before promotion')
    idle()
    if git('status','--porcelain','--untracked-files=no'):
        raise SystemExit('Tracked changes exist; refusing to overwrite')
    old=git('rev-parse','HEAD')
    git('fetch','/tmp/release.bundle','openai/live-noc-production')
    if git('rev-parse','FETCH_HEAD')!=sha:
        raise SystemExit('Candidate commit mismatch')
    git('merge-base','--is-ancestor',old,sha)
    backup=Path('/srv/inet-health-backups')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True)
    (backup/'commit').write_text(old+'\n')
    shutil.copy2('/etc/nginx/sites-available/default',backup/'nginx.default')
    for unit in ['inet-celery-worker','inet-celery-beat']:
        existing=Path('/etc/systemd/system')/(unit+'.service.d')/'20-live.conf'
        if existing.exists(): shutil.copy2(existing,backup/(unit+'.conf'))
    with tarfile.open(backup/'frontend.tar.gz','w:gz') as archive:
        if (root/'frontend/dist').exists(): archive.add(root/'frontend/dist',arcname='dist')
    if Path('/etc/inet-health/live.env').exists():
        shutil.copy2('/etc/inet-health/live.env',backup/'runtime.env')
    (backup/'manifest.json').write_text(json.dumps({'old':old,'new':sha,'frontend_sha256':frontend_sha,'bundle_sha256':bundle_sha,'host':socket.gethostname()},indent=2))
    print('ROLLBACK_BACKUP',backup,flush=True)
    run(['systemctl','stop','inet-celery-worker'])
    try:
        git('merge','--ff-only',sha)
        dist=root/'frontend/dist'
        dist.mkdir(exist_ok=True)
        with tarfile.open('/tmp/frontend.tar.gz') as archive:
            archive.extractall(dist,filter='data')
        # Hashed assets from the previous release stay available to open tabs.
        run(['chown','-R','david:david',str(dist)])
        for unit in ['inet-celery-worker','inet-celery-beat']:
            directory=Path('/etc/systemd/system')/(unit+'.service.d')
            directory.mkdir(exist_ok=True)
            (directory/'20-live.conf').write_text('[Service]\nEnvironmentFile=/etc/inet-health/live.env\nTimeoutStopSec=720\n')
        Path('/etc/inet-health').mkdir(exist_ok=True)
        Path('/etc/inet-health/live.env').write_text('BGP_WINDOW_SECONDS=60\n')
        # UAT had the default Debian page and no API proxy; production keeps
        # the same existing root and proxy, with explicit no-cache HTML.
        Path('/etc/nginx/sites-available/default').write_text('''server {
    listen 80 default_server;
    server_name _;
    root /home/david/inet-health-engine/frontend/dist;
    index index.html;
    location / { try_files $uri $uri/ /index.html; add_header Cache-Control "no-cache"; }
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 20s;
        add_header Cache-Control "no-store";
    }
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
''')
        run(['nginx','-t'])
        run(['systemctl','daemon-reload'])
        run(['systemctl','restart','inet-api','inet-celery-worker'])
        run(['systemctl','reload','nginx'])
        run(['systemctl','start','inet-celery-beat'])
        print('PROMOTED',sha,'rollback:',backup,flush=True)
    except Exception:
        print('PROMOTION_FAILED: use rollback command with',backup,flush=True)
        raise
elif action=='rollback':
    backup=Path(sys.argv[2]).resolve()
    if backup.parent!=Path('/srv/inet-health-backups'):
        raise SystemExit('Invalid rollback path')
    if subprocess.run(['systemctl','is-active','--quiet','inet-celery-beat']).returncode==0:
        raise SystemExit('Drain scheduler before rollback')
    idle()
    run(['systemctl','stop','inet-celery-worker'])
    git('checkout','--detach',(backup/'commit').read_text().strip())
    with tarfile.open(backup/'frontend.tar.gz') as archive:
        archive.extractall(root/'frontend',filter='data')
    shutil.copy2(backup/'nginx.default','/etc/nginx/sites-available/default')
    for unit in ['inet-celery-worker','inet-celery-beat']:
        path=Path('/etc/systemd/system')/(unit+'.service.d')/'20-live.conf'
        old=backup/(unit+'.conf')
        if old.exists():shutil.copy2(old,path)
        else:path.unlink(missing_ok=True)
    if (backup/'runtime.env').exists():
        shutil.copy2(backup/'runtime.env','/etc/inet-health/live.env')
    run(['systemctl','daemon-reload'])
    run(['nginx','-t'])
    run(['systemctl','restart','inet-api','inet-celery-worker'])
    run(['systemctl','reload','nginx'])
    run(['systemctl','start','inet-celery-beat'])
    print('ROLLED_BACK',flush=True)
else:
    raise SystemExit('Unknown action')
