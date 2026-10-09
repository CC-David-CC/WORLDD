import hashlib,json,pathlib,re,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
folder=root/'local/m02-participant-session-draft-03';source=folder/'source';build=folder/'debug/build'
manifest=json.loads((root/'local/participant-session-candidate-03.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
run=json.loads((folder/'run.json').read_text());assert len(run['results'])==4 and all(r['exit_code']==0 for r in run['results'])
counts={}
for label,suites,checks in [('sessions',2,32),('identity-regression',5,88)]:
    log=(folder/'debug'/(label+'.log')).read_text()
    values=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
    assert len(values)==suites and sum(values)==checks
    assert f'100% tests passed, 0 tests failed out of {suites}' in log and 'skipped=' not in log and '... skipped ' not in log
    counts[label]=checks
summary={'host':'sb16','profile':'Debug','new_checks':32,'regression_checks':88,'failed':0,'skipped':0,'source_files':238,
    'binaries':{n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in ('identityd','spatial-issuer-client')},'scope':'focused service authentication;full qualification pending'}
path=root/'local/participant-session-focused-summary-03.json';path.write_text(json.dumps(summary,indent=2)+'\n')
files=[(path,'summary-focused-03.json')]
for name in ('m02-session-client-baseline-01','m02-session-client-baseline-02','m02-participant-session-draft-03'):
    base=root/'local'/name
    for sub in (base,base/'debug'):
        if sub.exists():
            for p in sub.iterdir():
                if p.is_file() and p.suffix in ('.json','.log'):files.append((p,name+'/'+str(p.relative_to(base))))
with tarfile.open(root/'local/participant-session-focused-evidence-03.tar.gz','w:gz') as archive:
    for p,name in files:archive.add(p,arcname=name)
print(json.dumps(summary),flush=True)
