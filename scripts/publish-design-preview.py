"""Publish the isolated design entry on DEV1; preserve the existing app entry."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import tarfile

parser = argparse.ArgumentParser()
parser.add_argument('archive')
parser.add_argument('sha256')
args = parser.parse_args()
if socket.gethostname() != 'inet-health-dev1':
    raise SystemExit('Refusing publication outside inet-health-dev1')
def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()
archive = Path(args.archive)
if digest(archive) != args.sha256:
    raise SystemExit('Build archive hash mismatch')
root = Path('/var/www/html')
legacy_hash = digest(root / 'index.html')
base = Path('/srv/inet-health-design-r1')
release = base / 'releases' / args.sha256
release.mkdir(parents=True, exist_ok=True)
with tarfile.open(archive) as tar:
    tar.extractall(release, filter='data')
if 'Design Preview' not in (release / 'design.html').read_text():
    raise SystemExit('Expected isolated design entry missing')
files = [p for p in release.rglob('*') if p.is_file()
         and (p.relative_to(release).as_posix() == 'design.html'
              or p.relative_to(release).as_posix().startswith('assets/'))]
manifest_path = base / 'published.json'
prior = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
for source in files:
    relative = source.relative_to(release)
    target = root / relative
    if target.exists() and digest(target) != digest(source):
        if relative.as_posix() != 'design.html' or digest(target) != prior.get('files', {}).get('design.html'):
            raise SystemExit('Refusing to overwrite an unowned or changed file: ' + str(relative))
for source in files:
    target = root / source.relative_to(release)
    target.parent.mkdir(exist_ok=True, parents=True)
    temp = target.with_name(target.name + '.inet-preview-tmp')
    shutil.copyfile(source, temp)
    temp.chmod(0o644)
    temp.replace(target)
    if digest(target) != digest(source):
        raise SystemExit('Published file verification failed')
if digest(root / 'index.html') != legacy_hash:
    raise SystemExit('Legacy application entry changed unexpectedly')
manifest = {'archive_sha256':args.sha256,'legacy_index_sha256':legacy_hash,
            'files':{p.relative_to(release).as_posix():digest(p) for p in files},
            'preview_url':'http://192.168.1.65/design.html'}
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'published':len(files),'url':manifest['preview_url'],
                  'legacy_index_unchanged':True,'sha256':args.sha256}))
