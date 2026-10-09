import hashlib,json,os,socket,subprocess,sys,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
profile=sys.argv[1];assert profile in ('debug','runtime-sanitize')
root=r/'local/m02-native-witness-02';source=root/'source'
if not source.exists():
    source.mkdir(parents=True)
    with tarfile.open(r/'local/participant-fixtures-candidate-09.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/participant-fixtures-candidate-09.json').read_text())['files']
base=json.loads((r/'local/participant-fixtures-candidate-08.json').read_text())['files']
for n,h in manifest.items():
    assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
    if n=='CMakeLists.txt' or n.startswith(('src/','proto/','config/')):assert base[n]==h,n
build=r/f'local/m02-world-freshness-02/{profile}/build';assert (build/'spatial-security-auth').is_file()
out=root/profile;out.mkdir()
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
results=[]
for suite in ('test_native_launch','test_security_identity'):
    command=['python3',str(source/f'tests/native/{suite}.py'),str(build/'spatial-security-auth')]
    with (out/(suite+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(suite=suite,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,candidate=9,binary_candidate=8,production_inputs_identical=True,results=results),indent=2)+'\n');print(host,profile,suite,run.returncode,flush=True)
    if run.returncode:print((out/(suite+'.log')).read_text(),flush=True);raise SystemExit(run.returncode)
