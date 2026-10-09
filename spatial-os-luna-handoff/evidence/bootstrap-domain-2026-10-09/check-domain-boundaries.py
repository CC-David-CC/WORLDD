import hashlib,json,os,pathlib,shutil,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
previous=root/'local/m01-bootstrap-domain-build-01'
out=root/'local/m01-bootstrap-domain-focused-03';out.mkdir()
build=previous/'build'
for name,source in [('test_authority.py',previous/'source/tests/core/test_authority.py'),
                    ('test_authority_bootstrap.py',root/'local/test_authority_bootstrap-boundaries.py')]:
    shutil.copyfile(source,out/name)
command=['python3',str(out/'test_authority_bootstrap.py'),str(build/'spatial-authority-probe'),str(build/'spatial-authority-migrate')]
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(previous/'source'))
with (out/'core.log').open('wb') as log:
    result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=90)
report={'scope':'isolated candidate plus capacity and corrupted-binding boundaries','command':command,'exit_code':result.returncode,
        'inputs':{name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ('test_authority.py','test_authority_bootstrap.py')},
        'binaries':{name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in ('spatial-authority-probe','spatial-authority-migrate')}}
(out/'run.json').write_text(json.dumps(report,indent=2)+'\n')
print((out/'core.log').read_text()[-2500:])
raise SystemExit(result.returncode)
