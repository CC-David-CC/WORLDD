import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=root/'local/m02-participant-world-draft-01';out.mkdir();source=out/'source';source.mkdir();build=out/'debug/build'
manifest=json.loads((root/'local/participant-world-candidate-01.json').read_text())
with tarfile.open(root/'local/participant-world-candidate-01.tar') as archive:
    for m in archive.getmembers():assert m.isfile() and not pathlib.PurePosixPath(m.name).is_absolute() and '..' not in pathlib.PurePosixPath(m.name).parts
    archive.extractall(source,filter='data')
for n,h in manifest['files'].items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON']),('build',['cmake','--build',str(build),'--parallel','2']),('world',['ctest','--test-dir',str(build),'-V','-R','^world-participant$']),('worker',['ctest','--test-dir',str(build),'-V','-R','^identity-participant-runtime$'])]
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:r=subprocess.run(command,cwd=source,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=r.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,source_files=len(manifest['files']),results=results),indent=2)+'\n');print(host,name,r.returncode,flush=True)
    if r.returncode:print((out/(name+'.log')).read_text()[-9000:],flush=True);raise SystemExit(r.returncode)
