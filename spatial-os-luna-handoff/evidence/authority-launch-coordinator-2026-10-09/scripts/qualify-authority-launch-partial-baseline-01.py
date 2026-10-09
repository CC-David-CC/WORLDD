import hashlib,json,os,pathlib,socket,subprocess
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/m02-authority-launch-partial-baseline-01';out.mkdir()
source=r/'local/m02-authority-launch-coordinator-01/source';build=source.parent/'debug/build'
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
test=r/'local/test-authority-launch-partial-baseline-01.py'
command=['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-partial-baseline-01','--property=Delegate=yes',
    '--setenv=PYTHONPATH='+str(build/'python')+':'+str(source)+':'+str(source/'tests/native'),
    '--setenv=SPATIAL_TEST_XSERVER='+server['path'],'python3',str(test),str(build/'spatial-authority-launch-probe'),str(build/'spatial-security-auth')]
with (out/'baseline.log').open('wb') as log:p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=host,command=command,exit_code=p.returncode,test_sha256=hashlib.sha256(test.read_bytes()).hexdigest()),indent=2)+'\n')
text=(out/'baseline.log').read_text();print(text,flush=True)
assert p.returncode==1 and 'FAILED (failures=1)' in text and 'Invalid native cleanup record' in text
print(host,'retained native cleanup journal gap reproduced',flush=True)
