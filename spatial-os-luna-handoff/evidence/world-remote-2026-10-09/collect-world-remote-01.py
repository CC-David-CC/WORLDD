import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
base=root/'local/m01-world-remote-draft-01';source=base/'source';build=base/'build'
manifest=json.loads((root/'local/world-remote-source-01.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
run=json.loads((base/'run.json').read_text());assert [(r['name'],r['exit_code']) for r in run['results']]==[(n,0) for n in ('configure','build','remote','regressions')]
remote=(base/'remote.log').read_text();regressions=(base/'regressions.log').read_text()
assert re.findall(r'Ran (\d+) tests?',remote)==['5']
counts=re.findall(r'Ran (\d+) tests?',regressions);assert sorted(map(int,counts))==[6,15,20,27],counts
for log in (remote,regressions):assert '100% tests passed' in log and 'skipped=' not in log
security=root/'local/m01-world-remote-security-01';report=json.loads((security/'run.json').read_text())
assert report['exit_code']==0 and report['worldd_sha256']==hashlib.sha256((build/'worldd').read_bytes()).hexdigest()
assert report['test_sha256']==hashlib.sha256((security/'test_world_remote_security.py').read_bytes()).hexdigest()
assert 'Ran 13 tests' in (security/'tests.log').read_text() and '\nOK\n' in (security/'tests.log').read_text()
summary={'host':'sb16','profile':'isolated Debug','source_files':len(manifest['files']),
    'functional_tests':5,'security_tests':13,'local_regression_tests':68,'failed':0,'skipped':0,
    'binaries':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (build/'worldd',build/'identityd',build/'spatial-issuer-client')},
    'qualification':'focused only; no full matrix, second host, sanitizer, or cross-host qualification'}
summary_path=root/'local/world-remote-summary-01.json';summary_path.write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(root/'local/world-remote-evidence-01.tar.gz','w:gz') as archive:
    archive.add(summary_path,arcname='draft-01/sb16/summary.json')
    archive.add(root/'local/world-remote-source-01.json',arcname='draft-01/source-manifest.json')
    for name in manifest['changed_files']:archive.add(source/name,arcname='draft-01/source/'+name)
    for name in ('configure.log','build.log','remote.log','regressions.log','run.json'):archive.add(base/name,arcname='draft-01/sb16/'+name)
    for name in ('run.json','tests.log','test_world_remote_security.py'):archive.add(security/name,arcname='security-01/sb16/'+name)
print(json.dumps(summary),flush=True)
