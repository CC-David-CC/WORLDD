import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=root/'local/m02-process-termination-baseline-01';out.mkdir();source=out/'source';source.mkdir();build=out/'build'
manifest=json.loads((root/'local/process-termination-baseline-01.json').read_text())
with tarfile.open(root/'local/process-termination-baseline-01.tar') as a:
    for m in a.getmembers():assert m.isfile() and not pathlib.PurePosixPath(m.name).is_absolute() and '..' not in pathlib.PurePosixPath(m.name).parts
    a.extractall(source,filter='data')
for n,h in manifest['files'].items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON'],0),('build',['cmake','--build',str(build),'--parallel','2'],0),('baseline',['ctest','--test-dir',str(build),'-V','-R','^process-termination$'],8)]
results=[]
for name,command,expected in commands:
    with (out/(name+'.log')).open('wb') as log:r=subprocess.run(command,cwd=source,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=r.returncode,expected_exit_code=expected));(out/'run.json').write_text(json.dumps(dict(host=host,results=results,source_files=len(manifest['files'])),indent=2)+chr(10));print(host,name,r.returncode,flush=True)
    if r.returncode!=expected:print((out/(name+'.log')).read_text()[-10000:],flush=True);raise SystemExit(1)
print((out/'baseline.log').read_text()[-10000:],flush=True)
