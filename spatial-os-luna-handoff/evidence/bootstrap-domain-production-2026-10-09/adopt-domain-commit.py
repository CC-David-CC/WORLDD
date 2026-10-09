"""Preserve reviewed host edits, fast-forward, and verify the tested domain sources."""
import hashlib,json,pathlib,re,socket,subprocess,sys
repo=pathlib.Path('/home/dflanag3/src/spacial_desktop');root=repo/'spatial-os-luna-handoff'
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
target=sys.argv[1];assert re.fullmatch('[0-9a-f]{40}',target)
def git(*args): return subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True).stdout
for process in pathlib.Path('/proc').iterdir():
    if not process.name.isdigit(): continue
    try: command=(process/'cmdline').read_bytes().split(b'\0')
    except (FileNotFoundError,PermissionError,ProcessLookupError): continue
    assert not any(arg.endswith(b'/qualify-domain-production.py') for arg in command),'Domain qualification runner remains live'
summary=json.loads((root/'local/m01-domain-summary-final.json').read_text())
for label in ('debug','runtime-sanitize'):
    record=summary[label]
    assert record['core_checks']==973 and record['core_suites']==50 and record['harness_checks']==51 and record['P10_cases']==151
    assert record['failed']==record['skipped']==0
manifest=json.loads((root/'local/domain-production-tree-01.json').read_text())
def audit():
    for name,digest in manifest['files'].items():
        assert hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest,name
audit()
paths=['CMakeLists.txt','src/authority/store.cpp','src/authority/store.hpp','src/authority/migrate_main.cpp',
    'src/testing/authority_probe.cpp','proto/spatial/authority/v1/grants.proto','proto/spatial/authority/v1/grants_probe.proto',
    'tests/core/test_authority.py','tests/core/test_authority_bootstrap.py']
allowed={'spatial-os-luna-handoff/'+name for name in paths}
status=[line[3:].decode() for line in git('status','--porcelain=v1','-z').split(b'\0') if line]
assert status and set(status)<=allowed,status
before=git('rev-parse','HEAD').decode().strip();assert before==manifest['base_commit']
git('stash','push','--include-untracked','-m','Preserve qualified domain bootstrap source before integration','--',*sorted(allowed))
stash=git('rev-parse','refs/stash').decode().strip()
record=root/'local/m01-domain-integration.json'
ledger={'host':host,'before':before,'preserved_stash':stash,'target':target,'status':'stashed_before_fetch'}
record.write_text(json.dumps(ledger,indent=2)+'\n')
assert not git('status','--porcelain=v1').strip()
git('fetch','origin','main');assert git('rev-parse','origin/main').decode().strip()==target
git('merge','--ff-only',target);audit();assert not git('status','--porcelain=v1').strip()
ledger.update(status='passed',source_files=len(manifest['files']),head=git('rev-parse','HEAD').decode().strip())
record.write_text(json.dumps(ledger,indent=2)+'\n');print(json.dumps(ledger),flush=True)
