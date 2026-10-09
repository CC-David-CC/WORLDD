import json,os,pathlib,socket,subprocess
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/m02-authority-launch-durability-baseline-01';out.mkdir()
source=r/'local/m02-authority-prepared-launch-01/source';build=source.parent/'debug/build'
command=['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-durability-baseline-01','--property=Delegate=yes',
    '--setenv=PYTHONPATH='+str(build/'python')+':'+str(source),'python3',str(r/'local/test-authority-launch-durability-baseline-01.py'),str(source),str(build/'spatial-process-scope-probe')]
with (out/'baseline.log').open('wb') as log:p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=host,command=command,exit_code=p.returncode),indent=2)+'\n')
text=(out/'baseline.log').read_text();print(text,flush=True)
assert p.returncode==1 and 'FAILED (failures=1)' in text and 'Predecessor released execution without a durable launch record' in text
print(host,'expected missing durable launch boundary witnessed',flush=True)
