"""Preserve qualified host edits, fast-forward, and verify unchanged tested sources."""
import hashlib,json,pathlib,re,socket,subprocess,sys
repo=pathlib.Path('/home/dflanag3/src/spacial_desktop')
root=repo/'spatial-os-luna-handoff'
host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
target=sys.argv[1];assert re.fullmatch('[0-9a-f]{40}',target)
def git(*args):
    return subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True).stdout
for process in pathlib.Path('/proc').iterdir():
    if not process.name.isdigit(): continue
    try: command=(process/'cmdline').read_bytes().split(b'\0')
    except (FileNotFoundError,PermissionError,ProcessLookupError): continue
    assert not any(arg.endswith((b'/qualify-bootstrap-02.py',b'/queue-bootstrap-sb16-2.py')) for arg in command), 'Original qualification runner remains live'
for label in ('debug','runtime-sanitize'):
    report=json.loads((root/('local/m01-bootstrap-'+label+'-02/run.json')).read_text())
    assert [r['name'] for r in report['results']]==['configure','build','harness','core','P10']
    assert all(r['exit_code']==0 for r in report['results'])
manifest=json.loads((root/'local/bootstrap-tested-tree-02.json').read_text())
def audit():
    for name,digest in manifest['files'].items():
        assert hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest,name
audit()
paths=['CMakeLists.txt','proto/spatial/identity/v1/issuer.proto','src/authority/store.cpp','src/authority/store.hpp',
       'src/identity/issuer.cpp','src/identity/issuer.hpp','src/identity/issuer_client.cpp','src/identity/protocol.cpp',
       'src/identity/bootstrap.cpp','src/identity/bootstrap.hpp','tests/core/test_identity_enrollment.py',
       'tests/core/test_identity_revocation.py','tests/core/test_issuer_client.py','tests/core/test_identity_bootstrap.py',
       'tests/native/test_security_identity.py','tests/native/display_ready.py','tests/native/test_display_ready.py']
allowed={'spatial-os-luna-handoff/'+name for name in paths}
status=[line[3:].decode() for line in git('status','--porcelain=v1','-z').split(b'\0') if line]
assert status and set(status)<=allowed,status
before=git('rev-parse','HEAD').decode().strip()
git('stash','push','--include-untracked','-m','Preserve qualified bootstrap issuer source before integration','--',*sorted(allowed))
stash=git('rev-parse','refs/stash').decode().strip()
ledger={'host':host,'before':before,'preserved_stash':stash,'target':target,'status':'stashed_before_fetch'}
record=root/'local/m01-bootstrap-integration.json'
record.write_text(json.dumps(ledger,indent=2)+'\n')
assert not git('status','--porcelain=v1').strip()
git('fetch','origin','main')
assert git('rev-parse','origin/main').decode().strip()==target
git('merge','--ff-only',target)
audit()
assert not git('status','--porcelain=v1').strip()
ledger.update(status='passed',source_files=len(manifest['files']),head=git('rev-parse','HEAD').decode().strip())
record.write_text(json.dumps(ledger,indent=2)+'\n')
print(json.dumps(ledger),flush=True)
