import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host=='sb16'
source=r/'local/m02-participant-fixtures-01/source';source.mkdir(parents=True)
with tarfile.open(r/'local/participant-fixtures-candidate-01.tar') as archive:archive.extractall(source,filter='data')
manifest=json.loads((r/'local/participant-fixtures-candidate-01.json').read_text())
base=r/'local/m02-workspace-participant-focus-02/source';prior=json.loads((r/'local/workspace-participant-candidate-02.json').read_text())['files']
for name,digest in manifest['files'].items():
    assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
    assert hashlib.sha256((base/name).read_bytes()).hexdigest()==prior[name],name
    if not name.startswith('tests/'):assert digest==prior[name],name
build=base.parent/'debug/build';out=source.parent/'debug';out.mkdir()
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
inventory=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
tests={t['name']:t for t in inventory['tests']}
selected=['workspace-authority','world-authority','application-control-lease','broker-control-client-live','world-identity',
    'application-access','application-observation','world-application-control','workspace-bootstrap','workspace-bootstrap-races',
    'world-bootstrap','world-bootstrap-races','world-remote','world-remote-config','world-remote-races','world-remote-lifecycle',
    'world-remote-security','world-remote-native','world-participant','workspace-participant','workspace-participant-native']
results=[]
for name in selected:
    test=tests[name];command=[arg.replace(str(base),str(source)) for arg in test['command']]
    props={p['name']:p['value'] for p in test['properties']}
    env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
    for setting in props.get('ENVIRONMENT',[]):
        key,value=setting.split('=',1);env[key]=value.replace(str(base),str(source))
    with (out/(name+'.log')).open('wb') as log:
        try:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=props.get('TIMEOUT',180));code=run.returncode
        except subprocess.TimeoutExpired:code=124
    results.append(dict(name=name,command=command,exit_code=code));(out/'run.json').write_text(json.dumps(dict(host=host,results=results,selected=selected,server=server,source_files=len(manifest['files']),production_binaries='unchanged workspace02 Debug'),indent=2)+'\n')
    print(name,code,flush=True)
    if code:print((out/(name+'.log')).read_text()[-20000:],flush=True);raise SystemExit(code)
print('All selected migrated fixture suites passed',flush=True)
