import hashlib,json,pathlib,socket,subprocess,tarfile
repo=pathlib.Path('/home/dflanag3/src/spacial_desktop');root=repo/'spatial-os-luna-handoff'
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
def git(*args): return subprocess.check_output(['git',*args],cwd=repo).decode().strip()
manifest=json.loads((root/'local/domain-production-tree-01.json').read_text())
assert git('rev-parse','HEAD')==manifest['base_commit']
assert not git('status','--porcelain=v1'), 'Preserve unexpected host edits before installing candidate'
before=json.loads((root/'local/bootstrap-tested-tree-02.json').read_text())
for name,digest in before['files'].items():
    assert hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest,name
allowed={'CMakeLists.txt','src/authority/store.cpp','src/authority/store.hpp','src/authority/migrate_main.cpp',
         'src/testing/authority_probe.cpp','proto/spatial/authority/v1/grants.proto','proto/spatial/authority/v1/grants_probe.proto',
         'tests/core/test_authority.py','tests/core/test_authority_bootstrap.py'}
with tarfile.open(root/'local/domain-production-01.tar') as archive:
    members=archive.getmembers();assert len(members)==len(allowed) and {m.name for m in members}==allowed
    content={}
    for member in members:
        assert member.isfile()
        data=archive.extractfile(member).read()
        assert hashlib.sha256(data).hexdigest()==manifest['files'][member.name]
        assert member.name in before['files'] or not (root/member.name).exists(),member.name
        content[member.name]=data
    for name,data in content.items(): (root/name).write_bytes(data)
for name,digest in manifest['files'].items(): assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
record={'host':host,'status':'passed','source_files':len(manifest['files']),'base_commit':manifest['base_commit']}
(root/'local/m01-domain-source-audit-01.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
