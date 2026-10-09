import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');assert socket.gethostname().split('.')[0]=='sb16'
files=[]
for name in ('m02-participant-baseline-01','m02-participant-baseline-02','m02-participant-draft-01','m02-participant-draft-02','m02-participant-review-baseline-01'):
    folder=root/'local'/name
    for path in folder.iterdir():
        if path.is_file() and path.suffix in ('.log','.json'):files.append((path,name+'/'+path.name))
    for profile in ('debug','runtime-sanitize'):
        if (folder/profile).exists():
            for path in (folder/profile).iterdir():
                if path.is_file() and path.suffix in ('.log','.json'):files.append((path,name+'/'+profile+'/'+path.name))
record=json.loads((root/'local/m02-participant-draft-02/run.json').read_text())
assert len(record['results'])==8 and all(row['exit_code']==0 for row in record['results'])
summary={}
for profile in ('debug','runtime-sanitize'):
    totals={}
    for part in ('participants','identity-regression'):
        log=(root/'local/m02-participant-draft-02'/profile/(part+'.log')).read_text()
        counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
        assert counts and '100% tests passed, 0 tests failed' in log and 'skipped=' not in log and '... skipped ' not in log
        totals[part]={'checks':sum(counts),'suites':len(counts)}
    summary[profile]=totals
out=root/'local/participant-focused-01-summary.json';out.write_text(json.dumps(summary,indent=2)+'\n');files.append((out,'summary.json'))
with tarfile.open(root/'local/participant-baselines-focused-01.tar.gz','w:gz') as archive:
    for path,name in files:archive.add(path,arcname=name)
print(json.dumps(summary),flush=True)
