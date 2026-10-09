import os,json,subprocess,pathlib,socket,hashlib
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=p/'local/m04-world-application-control-focused-06';out.mkdir(exist_ok=False)
binary=json.loads((p/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
cmd=['ctest','--test-dir','build','-V','-R','^(world-application-control|world-identity|application-observation)$']
with (out/'test.log').open('w') as f:r=subprocess.run(cmd,cwd=p,env=env,stdout=f,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=socket.gethostname(),command=cmd,exit_code=r.returncode,binary=binary,worldd_sha256=hashlib.sha256((p/'build/worldd').read_bytes()).hexdigest()),indent=2)+'\n')
print(socket.gethostname(),r.returncode,flush=True)
print((out/'test.log').read_text()[-7000:])
raise SystemExit(r.returncode)
