import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
run=root/'local/m01-bootstrap-domain-build-01'
run.mkdir()
source=run/'source';source.mkdir()
with tarfile.open(root/'local/bootstrap-domain-build-01.tar') as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
    archive.extractall(source,filter='data')
manifest=json.loads((source/'source-manifest.json').read_text())
for name,digest in manifest.items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
build=run/'build'
report={'scope':'isolated candidate domain store; no world/workspace integration','source_files':len(manifest),'results':[]}
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug']),
          ('build',['cmake','--build',str(build),'--parallel','2','--target','spatial-authority-probe',
            'spatial-authority-migrate','python-protobuf','security-python-protobuf','grants-python-protobuf','grants-probe-python']),
          ('authority',['ctest','--test-dir',str(build),'-V','-R','^authority-(store|bootstrap)$'])]
for name,command in commands:
    with (run/(name+'.log')).open('wb') as output:
        result=subprocess.run(command,cwd=source,stdout=output,stderr=subprocess.STDOUT)
    report['results'].append({'name':name,'command':command,'exit_code':result.returncode})
    (run/'run.json').write_text(json.dumps(report,indent=2)+'\n')
    print(name,result.returncode,flush=True)
    if result.returncode: raise SystemExit(result.returncode)
for name,digest in manifest.items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
