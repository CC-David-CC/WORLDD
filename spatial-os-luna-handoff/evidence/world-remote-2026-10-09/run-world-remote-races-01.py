import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
source=root/'local/m01-world-remote-draft-01/source';out=root/'local/m01-world-remote-races-01';out.mkdir()
test=out/'test_world_remote_races.py';test.write_bytes((root/'local'/test.name).read_bytes())
results=[]
for label,folder in [('debug','m01-world-remote-draft-01'),('runtime-sanitize','m01-world-remote-runtime-sanitize-01')]:
    build=root/'local'/folder/'build'
    env=dict(os.environ,PYTHONPATH=':'.join(map(str,[build/'python',source,source/'tests/core'])),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    command=['python3',str(test),*[str(build/n) for n in ('identityd','spatial-issuer-client','worldd')]]
    with (out/(label+'.log')).open('wb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=90)
    results.append({'profile':label,'command':command,'exit_code':result.returncode,
        'worldd_sha256':hashlib.sha256((build/'worldd').read_bytes()).hexdigest()})
    (out/'run.json').write_text(json.dumps({'host':'sb16','results':results,
        'test_sha256':hashlib.sha256(test.read_bytes()).hexdigest()},indent=2)+'\n')
    print(label,result.returncode,flush=True);print((out/(label+'.log')).read_text()[-5000:],flush=True)
    if result.returncode:raise SystemExit(result.returncode)
