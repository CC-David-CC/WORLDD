import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
out=root/'local/m01-workspace-bootstrap-draft-01';out.mkdir();source=out/'source';source.mkdir();build=out/'build'
with tarfile.open(root/'local/workspace-bootstrap-draft-01.tar') as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
    archive.extractall(source,filter='data')
manifest=json.loads((source/'source-manifest.json').read_text())
for name,digest in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'))
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON']),
    ('build',['cmake','--build',str(build),'--parallel','2']),
    ('new-adapters',['ctest','--test-dir',str(build),'-V','-R','^(workspace-bootstrap|world-bootstrap|world-bootstrap-races|broker-bootstrap-client)$']),
    ('existing-regressions',['ctest','--test-dir',str(build),'-V','-R','^(workspace-authority|world-identity|world-authority|broker-control-client|broker-control-client-live)$'])]
report={'scope':'isolated world/workspace adapter draft','source_files':len(manifest),'results':[]}
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    report['results'].append({'name':name,'command':command,'exit_code':result.returncode})
    (out/'run.json').write_text(json.dumps(report,indent=2)+'\n');print(name,result.returncode,flush=True)
    if result.returncode:
        print((out/(name+'.log')).read_text()[-8500:],flush=True);raise SystemExit(result.returncode)
for name,digest in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
