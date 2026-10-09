import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=root/'local/m01-adapters-source-03';build=root/'local/m01-adapters-debug-03/build'
draft=root/'local/process-witness-draft';out=root/'local/process-witness-fix-01';out.mkdir()
audit=json.loads((draft/'source-audit.json').read_text())
assert hashlib.sha256((source/'tests/core/test_processes.py').read_bytes()).hexdigest()==audit['source_sha256']
assert hashlib.sha256((draft/'test_processes.py').read_bytes()).hexdigest()==audit['candidate_sha256']
env=dict(os.environ,PYTHONPATH=':'.join(map(str,[build/'python',source,source/'tests/core'])))
results=[]
for name,file,expected in [('baseline','baseline.py',1),('corrected','test_processes.py',0)]:
    command=['python3',str(draft/file),str(build/'spatial-test-host'),str(build/'workspaced'),str(build/'worldd')]
    with (out/(name+'.log')).open('wb') as log:
        run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
    results.append({'name':name,'command':command,'exit_code':run.returncode,'expected_exit_code':expected})
    (out/'run.json').write_text(json.dumps({'host':host,'results':results,'audit':audit},indent=2)+'\n')
    print(host,name,run.returncode,flush=True)
    assert run.returncode==expected,(out/(name+'.log')).read_text()
assert 'Ran 6 tests' in (out/'baseline.log').read_text() and 'FAILED (errors=1)' in (out/'baseline.log').read_text()
assert 'Ran 18 tests' in (out/'corrected.log').read_text() and '\nOK\n' in (out/'corrected.log').read_text()
with tarfile.open(root/('local/process-witness-evidence-'+host+'.tar.gz'),'w:gz') as archive:
    for path in out.iterdir():archive.add(path,arcname=host+'/'+path.name)
    if host=='sb16':
        for name in ('core.log','run.json'):
            archive.add(root/'local/m01-adapters-debug-03'/name,arcname=host+'/original-matrix/'+name)
print(host,'six fixture checks and twelve real process tests verified',flush=True)
