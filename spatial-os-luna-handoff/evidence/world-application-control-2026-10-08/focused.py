import os, sys, json, subprocess, hashlib, socket
from pathlib import Path
root=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
os.chdir(root)
out=root/'local'/sys.argv[1];out.mkdir(exist_ok=False)
build=root/(sys.argv[2] if len(sys.argv)>2 else 'build')
native=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(root),SPATIAL_TEST_XSERVER=native['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
cmd=['python3','tests/core/test_world_application_control.py',*[str(build/x) for x in ['identityd','spatial-issuer-client','worldd','spatial-security-auth']]]
if len(sys.argv)>3:cmd+=sys.argv[3:]
with (out/'test.log').open('w') as log:
    code=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600).returncode
report=dict(host=socket.gethostname(),base_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),command=cmd,exit_code=code,binary=native,worldd_sha256=hashlib.sha256((build/'worldd').read_bytes()).hexdigest(),test_sha256=hashlib.sha256((root/cmd[1]).read_bytes()).hexdigest())
(out/'run.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report));print((out/'test.log').read_text()[-18000:])
