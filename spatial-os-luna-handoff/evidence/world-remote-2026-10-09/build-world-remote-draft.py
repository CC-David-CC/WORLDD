import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
out=root/'local/m01-world-remote-draft-01';out.mkdir();source=out/'source';source.mkdir();build=out/'build'
with tarfile.open(root/'local/world-remote-source-01.tar') as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
    archive.extractall(source,filter='data')
manifest=json.loads((root/'local/world-remote-source-01.json').read_text())
def audit():
    for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
audit()
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],
    SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),
    SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'))
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON']),
    ('build',['cmake','--build',str(build),'--parallel','2']),
    ('remote',['ctest','--test-dir',str(build),'-V','-R','^world-remote$']),
    ('regressions',['ctest','--test-dir',str(build),'-V','-R','^(world-identity|world-authority|world-bootstrap|world-bootstrap-races)$'])]
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append({'name':name,'command':command,'exit_code':result.returncode})
    (out/'run.json').write_text(json.dumps({'host':'sb16','results':results,'source_files':len(manifest['files']),'server':server},indent=2)+'\n')
    print(name,result.returncode,flush=True)
    if result.returncode:
        print((out/(name+'.log')).read_text()[-6500:],flush=True);raise SystemExit(result.returncode)
audit();print('Isolated remote actor draft focused checks completed',flush=True)
