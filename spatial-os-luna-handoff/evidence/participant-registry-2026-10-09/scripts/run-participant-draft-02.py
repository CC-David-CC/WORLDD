import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host=='sb16'
out=root/'local/m02-participant-draft-02';out.mkdir();source=root/'local/m02-participant-draft-01/source'
manifest=json.loads((root/'local/participant-candidate-01.json').read_text())
def audit():
    for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
audit();record={'host':host,'source_files':len(manifest['files']),'results':[]}
for label,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    folder=out/label;folder.mkdir();build=root/'local/m02-participant-draft-01'/label/'build'
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
        UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),
        ('build',['cmake','--build',str(build),'--parallel','2','--target','identityd','spatial-issuer-client','spatial-key','spatial-issuer-watch','spatial-issuer-client-probe','spatial-issuer-watch-probe','python-protobuf','input-python-protobuf','security-python-protobuf','broker-python-protobuf','key-python-protobuf','issuer-python-protobuf','world-session-python-protobuf','grants-python-protobuf']),
        ('participants',['ctest','--test-dir',str(build),'-V','-R','^identity-participant(s|-client)$']),
        ('identity-regression',['ctest','--test-dir',str(build),'-V','-R','^(identity-|issuer-client|issuer-watch|actor-status)','-E','^identity-participant(s|-client)$'])]
    for name,command in commands:
        with (folder/(name+'.log')).open('wb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        record['results'].append({'profile':label,'name':name,'command':command,'exit_code':result.returncode})
        (out/'run.json').write_text(json.dumps(record,indent=2)+'\n');print(host,label,name,result.returncode,flush=True)
        if result.returncode:
            print((folder/(name+'.log')).read_text()[-8000:],flush=True);raise SystemExit(result.returncode)
    audit()
print('Participant draft02 focused Debug and runtime sanitizer profiles complete; full qualification remains required',flush=True)
