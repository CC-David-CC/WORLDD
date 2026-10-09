import hashlib,json,os,pathlib,socket,subprocess,sys,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
profile=sys.argv[1];assert profile in ('debug','runtime-sanitize')
out=root/'local/m02-participant-world-draft-03';out.mkdir(exist_ok=True);source=out/'source'
manifest=json.loads((root/'local/participant-world-candidate-03.json').read_text());old=json.loads((root/'local/participant-world-candidate-02.json').read_text())
assert [n for n,h in manifest['files'].items() if h!=old['files'].get(n)]==['tests/core/test_world_participant_native.py']
if not source.exists():
    source.mkdir()
    with tarfile.open(root/'local/participant-world-candidate-03.tar') as archive:
        for m in archive.getmembers():assert m.isfile() and not pathlib.PurePosixPath(m.name).is_absolute() and '..' not in pathlib.PurePosixPath(m.name).parts
        archive.extractall(source,filter='data')
for n,h in manifest['files'].items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
base=root/'local/m02-participant-world-draft-02'
for n,h in old['files'].items():assert hashlib.sha256((base/'source'/n).read_bytes()).hexdigest()==h,n
build=base/profile/'build';folder=out/profile;folder.mkdir()
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_TEST_XSERVER_LOG=str(folder/'xserver.log'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
command=['python3',str(source/'tools/with_test_display.py'),'--','python3',str(source/'tests/core/test_world_participant_native.py'),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]
with (folder/'native.log').open('wb') as log:result=subprocess.run(command,env=env,cwd=source,stdout=log,stderr=subprocess.STDOUT)
(folder/'run.json').write_text(json.dumps(dict(host=host,profile=profile,command=command,exit_code=result.returncode,server=server,source_files=len(manifest['files']),binary_source='participant-world-candidate-02',binaries={n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in ['worldd','identityd','spatialwm','spatial-xprobe']}),indent=2)+'\n')
print((folder/'native.log').read_text(),flush=True);raise SystemExit(result.returncode)
