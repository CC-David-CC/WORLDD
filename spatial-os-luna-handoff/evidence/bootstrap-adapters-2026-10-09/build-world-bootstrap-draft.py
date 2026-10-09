import hashlib,json,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
out=root/'local/m01-world-bootstrap-draft-01';out.mkdir();source=out/'source';source.mkdir();build=out/'build'
with tarfile.open(root/'local/world-bootstrap-draft-01.tar') as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
    archive.extractall(source,filter='data')
manifest=json.loads((source/'source-manifest.json').read_text())
for name,digest in manifest.items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
report={'scope':'isolated world bootstrap candidate; workspace adapter remains explicitly unimplemented','source_files':len(manifest),'results':[]}
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug']),
    ('build',['cmake','--build',str(build),'--parallel','2','--target','worldd','identityd','spatial-issuer-client',
        'python-protobuf','security-python-protobuf','key-python-protobuf','issuer-python-protobuf','world-session-python-protobuf','grants-python-protobuf']),
    ('world-bootstrap',['ctest','--test-dir',str(build),'-V','-R','^world-bootstrap$'])]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:
        result=subprocess.run(command,cwd=source,stdout=log,stderr=subprocess.STDOUT)
    report['results'].append({'name':name,'command':command,'exit_code':result.returncode})
    (out/'run.json').write_text(json.dumps(report,indent=2)+'\n')
    print(name,result.returncode,flush=True)
    if result.returncode:
        print((out/(name+'.log')).read_text()[-6000:],flush=True);raise SystemExit(result.returncode)
for name,digest in manifest.items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
