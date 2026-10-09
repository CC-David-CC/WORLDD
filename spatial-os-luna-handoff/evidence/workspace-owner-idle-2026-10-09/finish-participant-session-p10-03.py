import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host=='sb16-2'
source=root/'local/m02-participant-session-source-03';build=root/'local/m02-participant-session-runtime-sanitize-03/build'
manifest=json.loads((root/'local/participant-session-candidate-03.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
fixed=json.loads((root/'local/workspace-relay-fix-validation-01/run.json').read_text());assert [x['exit_code'] for x in fixed['results']]==[1,0,0]
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
out=root/'local/m02-participant-session-runtime-sanitize-p10-completion-03';out.mkdir()
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
command=['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')]
with (out/'P10.log').open('wb') as log:r=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(host=host,command=command,exit_code=r.returncode,server=server,source_manifest_sha256=hashlib.sha256((root/'local/participant-session-candidate-03.json').read_bytes()).hexdigest()),indent=2)+'\n');print('P10 completion',r.returncode,flush=True)
raise SystemExit(r.returncode)
