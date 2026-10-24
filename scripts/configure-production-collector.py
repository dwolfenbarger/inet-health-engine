"""Prepare public collector configuration and a private development archive destination."""
import argparse,importlib.util,json,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('nas',HERE/'nas-quota.py');nas=importlib.util.module_from_spec(spec);spec.loader.exec_module(nas)
def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=60).stdout.strip()
p=argparse.ArgumentParser();p.add_argument('--activate',action='store_true');args=p.parse_args()
with nas.quota():
 if args.activate:
  policy=[{'ct':554,'namespace':'uat554','database':'inethealth'},{'ct':553,'namespace':'prod553','database':'inethealth'}]
  path=Path('/etc/inet-health-journal/sources.json');temp=path.with_suffix('.pending');temp.write_text(json.dumps(policy));temp.replace(path)
  run('systemctl','enable','--now','inet-journal-rotation.timer')
  print('DEVELOPMENT_ARCHIVE_SOURCES',json.dumps(policy),flush=True)
 else:
  role=json.loads((nas.ROOT/'quota.json').read_text());role.update({'source_ct':553,'source_environment':'production',
   'archive_destination':'development/prod553','full_archive_access':'development-only','public_bulk_archive_api':False})
  with tempfile.TemporaryDirectory() as temporary:
   marker=Path(temporary)/'role.json';marker.write_text(json.dumps(role))
   run('pct','push','553',str(marker),'/etc/inet-health/journal-nas-ready.json')
   for name in ['routing-observers.json','atlas-mesh.json']:
    path=Path(temporary)/name;run('pct','pull','554','/etc/inet-health/'+name,str(path));run('pct','push','553',str(path),'/etc/inet-health/'+name)
  print('PRODUCTION_OBSERVER_CONFIGURED_PRIVATE_DEVELOPMENT_ARCHIVE',flush=True)
