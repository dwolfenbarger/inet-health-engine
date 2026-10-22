import os,subprocess
from pathlib import Path
assert os.uname().nodename in ['inet-health-uat','inet-health-prod']
def run(args): subprocess.run(args,check=True)
run(['apt-get','update','-qq'])
run(['apt-get','install','-y','ca-certificates','curl','gnupg','rsync'])
key=Path('/tmp/nlnetlabs-aptkey.asc')
run(['curl','-fsSL','--max-time','30','https://packages.nlnetlabs.nl/aptkey.asc','-o',str(key)])
run(['gpg','--batch','--yes','--dearmor','-o','/usr/share/keyrings/nlnetlabs-archive-keyring.gpg',str(key)])
Path('/etc/apt/sources.list.d/nlnetlabs.list').write_text('deb [arch=amd64 signed-by=/usr/share/keyrings/nlnetlabs-archive-keyring.gpg] https://packages.nlnetlabs.nl/linux/debian trixie main\n')
run(['apt-get','update','-qq'])
# Official package defaults bind both services to loopback. Pre-create explicitly too.
Path('/etc/routinator').mkdir(exist_ok=True)
conf=Path('/etc/routinator/routinator.conf')
if conf.exists(): raise SystemExit('Existing configuration requires review')
conf.write_text('repository-dir = "/var/lib/routinator/rpki-cache"\nrtr-listen = ["127.0.0.1:3323"]\nhttp-listen = ["127.0.0.1:8323"]\nvalidation-threads = 2\n')
os.environ['DEBIAN_FRONTEND']='noninteractive'
run(['apt-get','-o','Dpkg::Options::=--force-confold','install','-y','routinator'])
run(['systemctl','enable','--now','routinator'])
run(['systemctl','is-active','routinator'])
run(['ss','-lntp'])
