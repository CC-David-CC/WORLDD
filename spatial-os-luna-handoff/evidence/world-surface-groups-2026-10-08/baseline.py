import pathlib,os,json,subprocess
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=p/'local/m04-surface-groups-baseline-04';out.mkdir(exist_ok=False)
binary=json.loads((p/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(p/'build/python')+':'+str(p),SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
cmd=['python3','tests/native/test_world_surface_groups.py',str(p/'build/spatial-security-auth')]
with (out/'test.log').open('w') as f:r=subprocess.run(cmd,cwd=p,env=env,stdout=f,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(command=cmd,binary=binary,exit_code=r.returncode),indent=2)+'\n')
print((out/'test.log').read_text()[-9000:],flush=True)
