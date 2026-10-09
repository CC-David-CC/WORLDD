import hashlib,json,os,pathlib,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
draft=root/'local/m01-workspace-bootstrap-draft-01';source=draft/'source';build=draft/'build'
out=root/'local/m01-workspace-bootstrap-corrected-02';out.mkdir()
test=out/'test_workspace_bootstrap.py';test.write_bytes((root/'local'/test.name).read_bytes())
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env.update(SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'))
commands=[('workspace-bootstrap',['python3',str(test),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]),
    ('existing-regressions',['ctest','--test-dir',str(build),'-V','-R','^(workspace-authority|world-identity|world-authority|broker-control-client|broker-control-client-live)$'])]
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append({'name':name,'command':command,'exit_code':result.returncode})
    (out/'run.json').write_text(json.dumps({'scope':'corrected draft tests; unchanged candidate binaries',
        'results':results,'test_sha256':hashlib.sha256(test.read_bytes()).hexdigest()},indent=2)+'\n')
    print(name,result.returncode,flush=True)
    if result.returncode:
        print((out/(name+'.log')).read_text()[-6500:],flush=True);raise SystemExit(result.returncode)
