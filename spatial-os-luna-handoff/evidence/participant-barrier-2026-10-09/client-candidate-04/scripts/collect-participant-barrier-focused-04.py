import hashlib,json,pathlib,re,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');source=root/'local/m02-participant-barrier-draft-03/source';build=root/'local/m02-participant-barrier-draft-03/debug/build'
manifest=json.loads((root/'local/participant-barrier-candidate-04.json').read_text());test=root/'local/test_identity_participant_barrier_client_04.py'
for name,digest in manifest['files'].items():
    p=test if name=='tests/core/test_identity_participant_barrier_client.py' else source/name
    assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
folder=root/'local/m02-participant-barrier-draft-03';run=json.loads((folder/'run.json').read_text());assert len(run['results'])==4 and all(r['exit_code']==0 for r in run['results'])
for label,suites,checks in [('sessions',2,29),('identity-regression',10,187)]:
    log=(folder/'debug'/(label+'.log')).read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
    assert len(counts)==suites and sum(counts)==checks,(label,counts)
    assert f'100% tests passed, 0 tests failed out of {suites}' in log and 'skipped=' not in log and '... skipped ' not in log
folder=root/'local/m02-participant-barrier-client-focused-04';run=json.loads((folder/'run.json').read_text());assert len(run['results'])==1 and run['results'][0]['exit_code']==0
log=(folder/'client.log').read_text();assert 'Ran 14 tests in ' in log and 'OK' in log and 'skipped=' not in log
summary={'host':'sb16','profile':'Debug','new_server_checks':19,'new_client_checks':14,'new_unique_checks':33,'regression_checks':187,'failed':0,'skipped':0,'source_files':240,
    'binaries':{name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in ('identityd','spatial-issuer-client')},'scope':'focused issuer and checked-client component; full qualification and actual cancellation effect wiring pending'}
path=root/'local/participant-barrier-focused-summary-04.json';path.write_text(json.dumps(summary,indent=2)+'\n');files=[(path,'summary-focused-04.json')]
for label in ('m02-barrier-client-baseline-01','m02-participant-barrier-draft-03','m02-participant-barrier-client-focused-04','m02-participant-barrier-audit-04'):
    base=root/'local'/label
    for sub in (base,base/'debug'):
        if sub.exists():
            for p in sub.iterdir():
                if p.is_file() and p.suffix in ('.json','.log'):files.append((p,label+'/'+str(p.relative_to(base))))
with tarfile.open(root/'local/participant-barrier-focused-evidence-04.tar.gz','w:gz') as archive:
    for p,name in files:archive.add(p,arcname=name)
print(json.dumps(summary),flush=True)
