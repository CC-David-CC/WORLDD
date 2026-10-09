import hashlib,json,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/m02-authority-launch-fixture-03';out.mkdir()
source=r/'local/m02-authority-launch-coordinator-02/source';build=source.parent/'debug/build'
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
with tarfile.open(r/'local/authority-launch-coordinator-candidate-03.tar') as a:
    data=a.extractfile('tests/native/test_authority_launch.py').read()
test=out/'test_authority_launch.py';test.write_bytes(data)
command=['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-coordinator-fixture-03','--property=Delegate=yes',
    '--setenv=PYTHONPATH='+str(build/'python')+':'+str(source)+':'+str(source/'tests/native'),
    '--setenv=SPATIAL_TEST_XSERVER='+server['path'],'python3',str(test),str(build/'spatial-authority-launch-probe'),str(build/'spatial-security-auth')]
with (out/'fixture.log').open('wb') as log:p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=host,command=command,exit_code=p.returncode,test_sha256=hashlib.sha256(data).hexdigest()),indent=2)+'\n')
text=(out/'fixture.log').read_text();print(text,flush=True)
assert p.returncode==0 and 'Ran 14 tests' in text and '\nOK\n' in text
print(host,'corrected fixture passed all14 checks against unchanged candidate02 production',flush=True)
