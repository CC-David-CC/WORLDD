import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=root/'local/m02-workspace-participant-01/source';source.mkdir(parents=True)
with tarfile.open(root/'local/workspace-participant-candidate-01.tar') as archive:archive.extractall(source,filter='data')
manifest=json.loads((root/'local/workspace-participant-candidate-01.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
out=source.parent/'debug';out.mkdir();build=out/'build'
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS=OFF']),
('build',['cmake','--build',str(build),'--parallel','1']),
('workspace',['ctest','--test-dir',str(build),'-V','-R','^workspace-participant$']),
('related',['ctest','--test-dir',str(build),'-V','-R','^(world-participant|identity-participant-runtime|process-termination)$'])]
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,results=results,source_files=len(manifest['files'])),indent=2)+'\n')
    print(host,name,run.returncode,flush=True)
    if run.returncode:print((out/(name+'.log')).read_text()[-13000:],flush=True);raise SystemExit(run.returncode)
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
print(host,'focused workspace participation passed',flush=True)
