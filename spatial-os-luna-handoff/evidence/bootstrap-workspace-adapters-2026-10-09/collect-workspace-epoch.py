import hashlib,json,pathlib,re,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
fixed=root/'local/m02-workspace-epoch-fixed-02';report=json.loads((fixed/'run.json').read_text())
assert [r['name'] for r in report['results']]==['configure','build','workspace-regressions','races']
assert all(r['exit_code']==0 for r in report['results'])
log=(fixed/'workspace-regressions.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
assert len(counts)==3 and sum(counts)==52 and '100% tests passed, 0 tests failed out of 3' in log
races=(fixed/'races.log').read_text();assert re.search(r'Ran 10 tests in ',races) and '\nOK\n' in races
assert 'skipped=' not in log+races and '... skipped ' not in log+races
source=fixed/'source';manifest=json.loads((source/'source-manifest.json').read_text())
for name,digest in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
summary={'host':'sb16','workspace_broker_checks':52,'revocation_checks':10,'failed':0,'skipped':0,
    'scope':'isolated Debug correction; full both-host Debug and runtime sanitizer qualification pending'}
record=root/'local/workspace-epoch-summary.json';record.write_text(json.dumps(summary,indent=2)+'\n')
files=[(record,'sb16/workspace-epoch-summary.json')]
for folder in (root/'local/m02-workspace-epoch-baseline-01',fixed):
    for path in folder.iterdir():
        if path.is_file() and path.suffix in ('.json','.log','.py'):files.append((path,'sb16/'+folder.name+'/'+path.name))
for name in ('src/workspace/authority.cpp','tests/core/test_workspace_bootstrap_races.py','source-manifest.json'):
    files.append((source/name,'sb16/'+fixed.name+'/source/'+name))
with tarfile.open(root/'local/workspace-epoch-evidence-sb16.tar.gz','w:gz') as archive:
    for path,name in files:archive.add(path,arcname=name)
print(json.dumps(summary),flush=True)
