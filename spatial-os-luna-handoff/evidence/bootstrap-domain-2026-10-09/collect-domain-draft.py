import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
files=[]
for attempt in ('m01-bootstrap-domain-syntax-01','m01-bootstrap-domain-build-01','m01-bootstrap-domain-focused-02','m01-bootstrap-domain-focused-03','m01-bootstrap-domain-sanitize-01','m01-bootstrap-domain-audit-01'):
    directory=root/'local'/attempt
    for file in directory.iterdir():
        if file.is_file() and file.suffix in ('.log','.json','.py','.descriptor'):
            files.append((file,'sb16/'+attempt+'/'+file.name))
    inputs=directory/'inputs'
    if inputs.exists():
        for file in inputs.iterdir():
            if file.is_file(): files.append((file,'sb16/'+attempt+'/inputs/'+file.name))
sanitizer=root/'local/m01-bootstrap-domain-sanitize-01'
report=json.loads((sanitizer/'run.json').read_text())
assert [r['name'] for r in report['results']]==['configure','build','authority']
assert all(r['exit_code']==0 for r in report['results'])
log=(sanitizer/'authority.log').read_text()
assert re.findall(r'^\d+: Ran (\d+) tests in ',log,re.M)==['43','32']
assert '100% tests passed, 0 tests failed out of 2' in log and 'skipped=' not in log
hashes={name:hashlib.sha256((sanitizer/'build'/name).read_bytes()).hexdigest()
        for name in ('spatial-authority-probe','spatial-authority-migrate')}
(sanitizer/'binaries.json').write_text(json.dumps(hashes,indent=2)+'\n')
files.append((sanitizer/'binaries.json','sb16/m01-bootstrap-domain-sanitize-01/binaries.json'))
files.append((sanitizer/'source/source-manifest.json','sb16/m01-bootstrap-domain-sanitize-01/source-manifest.json'))
source=root/'local/m01-bootstrap-domain-build-01/source'
for name in ('source-manifest.json','CMakeLists.txt','src/authority/store.cpp','src/authority/store.hpp',
             'src/authority/migrate_main.cpp','src/testing/authority_probe.cpp','proto/spatial/authority/v1/grants.proto',
             'proto/spatial/authority/v1/grants_probe.proto','tests/core/test_authority.py','tests/core/test_authority_bootstrap.py'):
    files.append((source/name,'sb16/m01-bootstrap-domain-build-01/inputs/'+name))
with tarfile.open(root/'local/bootstrap-domain-evidence-sb16.tar.gz','w:gz') as archive:
    for name,file in {name:file for file,name in files}.items(): archive.add(file,arcname=name)
print(len(files),'regular evidence and source files; no runtime databases or binaries')
