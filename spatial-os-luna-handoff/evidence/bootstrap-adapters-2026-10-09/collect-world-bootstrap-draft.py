import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
draft=root/'local/m01-world-bootstrap-draft-01'
report=json.loads((draft/'run.json').read_text())
assert [r['name'] for r in report['results']]==['configure','build','world-bootstrap']
assert all(r['exit_code']==0 for r in report['results'])
assert re.search(r'Ran 15 tests in ',(draft/'world-bootstrap.log').read_text())
assert '100% tests passed, 0 tests failed' in (draft/'world-bootstrap.log').read_text()
manifest=json.loads((draft/'source/source-manifest.json').read_text())
for name,digest in manifest.items(): assert hashlib.sha256((draft/'source'/name).read_bytes()).hexdigest()==digest,name
final=root/'local/m01-world-bootstrap-races-02'
assert json.loads((final/'run.json').read_text())['exit_code']==0
assert re.search(r'Ran 6 tests in ',(final/'tests.log').read_text())
summary={'world_cases':15,'additional_cases':6,'failed_final':0,'skipped_final':0,
    'host':'sb16','build':'isolated Debug','source_files':len(manifest),
    'preserved_failure':'First six-case attempt failed on an incorrect database filename and overly narrow parent-revocation error expectation.',
    'limits':['Workspace adapter unimplemented','Full regression and sanitizer profiles pending','No production integration']}
record=root/'local/world-bootstrap-draft-summary.json';record.write_text(json.dumps(summary,indent=2)+'\n')
files=[(record,'sb16/world-draft-summary.json')]
for folder in (draft,root/'local/m01-world-bootstrap-races-01',final):
    for path in folder.iterdir():
        if path.is_file() and path.suffix in ('.json','.log','.py'):
            files.append((path,'sb16/'+folder.name+'/'+path.name))
files.append((draft/'source/source-manifest.json','sb16/'+draft.name+'/source-manifest.json'))
# Preserve the exact reviewed draft separately from the production sources.
for name in ('src/world/authority.cpp','src/world/authority.hpp','src/world/identity_sessions.cpp',
    'proto/spatial/authority/v1/grants.proto','proto/spatial/identity/v1/world_session.proto',
    'proto/spatial/workspace/v1/broker.proto','tests/core/test_world_bootstrap.py','CMakeLists.txt'):
    files.append((draft/'source'/name,'sb16/'+draft.name+'/source/'+name))
with tarfile.open(root/'local/world-bootstrap-draft-evidence-sb16.tar.gz','w:gz') as archive:
    for path,name in files: archive.add(path,arcname=name)
print(json.dumps(summary),flush=True)
