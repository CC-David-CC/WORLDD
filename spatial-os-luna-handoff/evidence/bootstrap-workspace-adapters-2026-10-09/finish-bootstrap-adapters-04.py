"""Collect completed candidate 04, then qualify the separate process witness with its exact binaries."""
import ast,hashlib,json,os,pathlib,socket,subprocess,tarfile,time
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=root/'local/m01-adapters-finish-04';out.mkdir()
record={'host':host,'status':'waiting','pid':os.getpid(),'results':[]}
def save(): (out/'run.json').write_text(json.dumps(record,indent=2)+'\n')
def active():
    pids=[]
    for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        try:args=path.read_bytes().split(b'\0')
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
        if any(arg.endswith((b'/qualify-bootstrap-adapters-04.py',b'/queue-bootstrap-adapters-04.py')) for arg in args):
            pids.append(int(path.parent.name))
    return pids
def run(name,command,cwd,env=None):
    with (out/(name+'.log')).open('wb') as log:
        completed=subprocess.run(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
    record['results'].append({'name':name,'command':command,'exit_code':completed.returncode});save()
    assert completed.returncode==0,(name,(out/(name+'.log')).read_text()[-5000:])
try:
    fix=json.loads((root/'evidence/process-witness-exit-2026-10-09/source-audit.json').read_text())
    fixture=(root/'tests/core/test_processes.py').read_bytes()
    assert hashlib.sha256(fixture).hexdigest()==fix['candidate_sha256']
    (out/'test_processes.py').write_bytes(fixture)
    record['waiting_for']=active();save();print(host,'waiting for candidate 04 runners',record['waiting_for'],flush=True)
    while active():time.sleep(1)
    record['status']='collecting';save()
    run('matrix-collector',['python3',str(root/'local/collect-bootstrap-adapters-04.py'),'final'],root)
    source=root/'local/m01-adapters-source-04'
    manifest=json.loads((root/'local/adapters-tested-tree-04.json').read_text())
    assert manifest['files']['tests/core/test_processes.py']==fix['source_sha256']
    def original_tests(data):
        cls=next(n for n in ast.parse(data).body if isinstance(n,ast.ClassDef) and n.name=='ProcessTests')
        return {n.name:ast.dump(n,include_attributes=False) for n in cls.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
    assert original_tests(fixture)==original_tests((source/'tests/core/test_processes.py').read_bytes())
    summary=json.loads((root/'local/m01-adapters-summary-final-04.json').read_text())
    record['status']='witness-verification';save()
    for label in ('debug','runtime-sanitize'):
        build=root/('local/m01-adapters-'+label+'-04/build')
        env=dict(os.environ,PYTHONPATH=':'.join(map(str,[build/'python',source,source/'tests/core'])),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        expected=summary[label]['binaries']
        def binaries_match():
            for name,digest in expected.items():assert hashlib.sha256((build/name).read_bytes()).hexdigest()==digest,name
        binaries_match()
        run(label,['python3',str(out/'test_processes.py'),str(build/'spatial-test-host'),str(build/'workspaced'),str(build/'worldd')],source,env)
        log=(out/(label+'.log')).read_text()
        assert 'Ran 18 tests' in log and '\nOK\n' in log and 'skipped=' not in log
        binaries_match();print(host,label,'corrected witness and all twelve real process tests passed',flush=True)
    for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
    manifest['files']['tests/core/test_processes.py']=fix['candidate_sha256']
    manifest['qualification']='Complete candidate 04 matrix plus separately verified process witness with identical executables'
    (out/'integration-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    record.update(status='passed',original_matrix_core_checks=1037,additional_witness_checks=6,
        rerun_original_process_checks=12,original_test_bodies_unchanged=12,production_integration='pending')
    save()
    with tarfile.open(root/('local/adapters-finish-evidence-'+host+'-04.tar.gz'),'w:gz') as archive:
        for path in out.iterdir():
            if path.is_file():archive.add(path,arcname=host+'/finish-04/'+path.name)
    print(host,'candidate 04 and corrected witness qualified; integration pending',flush=True)
except BaseException as error:
    record.update(status='failed',error=str(error));save();raise
