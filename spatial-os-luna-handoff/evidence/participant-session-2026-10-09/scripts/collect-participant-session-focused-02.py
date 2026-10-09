import json,pathlib,re,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
files=[]
for label in ('m02-participant-session-baseline-01','m02-participant-session-draft-01','m02-participant-session-focused-02'):
    folder=root/'local'/label
    for sub in (folder,folder/'debug'):
        if sub.exists():
            for path in sub.iterdir():
                if path.is_file() and path.suffix in ('.json','.log'):files.append((path,label+'/'+str(path.relative_to(folder))))
focused=root/'local/m02-participant-session-focused-02'
record=json.loads((focused/'run.json').read_text());assert len(record['results'])==2 and all(r['exit_code']==0 for r in record['results'])
sessions=(focused/'sessions.log').read_text();assert 'Ran 20 tests in ' in sessions and 'OK' in sessions and 'skipped=' not in sessions
regression=(focused/'regression.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',regression,re.M)]
assert len(counts)==5 and sum(counts)==88 and '100% tests passed, 0 tests failed out of 5' in regression and 'skipped=' not in regression
summary={'host':'sb16','profile':'Debug','new_checks':20,'regression_checks':88,'regression_suites':5,'failed':0,'skipped':0,'source_files':237,'identityd_sha256':record['identityd_sha256'],'scope':'server proof only; checked-client and full qualification pending'}
path=root/'local/participant-session-focused-summary-02.json';path.write_text(json.dumps(summary,indent=2)+'\n');files.append((path,'summary-focused-02.json'))
with tarfile.open(root/'local/participant-session-focused-evidence-02.tar.gz','w:gz') as archive:
    for path,name in files:archive.add(path,arcname=name)
print(json.dumps(summary),flush=True)
