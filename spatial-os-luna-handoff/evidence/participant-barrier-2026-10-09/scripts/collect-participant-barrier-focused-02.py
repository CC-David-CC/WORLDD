import hashlib,json,pathlib,re,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');folder=root/'local/m02-participant-barrier-focused-02'
manifest=json.loads((root/'local/participant-barrier-candidate-02.json').read_text());source=root/'local/m02-participant-barrier-draft-01/source';test=root/'local/test_identity_participant_barrier_02.py'
for name,digest in manifest['files'].items():
    p=test if name=='tests/core/test_identity_participant_barrier.py' else source/name
    assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
run=json.loads((folder/'run.json').read_text());assert len(run['results'])==2 and all(r['exit_code']==0 for r in run['results'])
log=(folder/'barriers.log').read_text();assert 'Ran 19 tests in ' in log and 'OK' in log and 'skipped=' not in log
log=(folder/'regression.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
assert len(counts)==10 and sum(counts)==187,counts
assert '100% tests passed, 0 tests failed out of 10' in log and 'skipped=' not in log and '... skipped ' not in log
summary={'host':'sb16','profile':'Debug','new_checks':19,'regression_checks':187,'failed':0,'skipped':0,'source_files':239,'identityd_sha256':run['identityd_sha256'],'scope':'issuer persistence draft; checked-client and full qualification pending; actual cancellation effect wiring pending'}
path=root/'local/participant-barrier-focused-summary-02.json';path.write_text(json.dumps(summary,indent=2)+'\n');files=[(path,'summary-focused-02.json')]
for label in ('m02-participant-barrier-baseline-01','m02-participant-barrier-draft-01','m02-participant-barrier-focused-02','m02-participant-barrier-audit-02'):
    base=root/'local'/label
    for sub in (base,base/'debug'):
        if sub.exists():
            for p in sub.iterdir():
                if p.is_file() and p.suffix in ('.json','.log'):files.append((p,label+'/'+str(p.relative_to(base))))
with tarfile.open(root/'local/participant-barrier-focused-evidence-02.tar.gz','w:gz') as archive:
    for p,name in files:archive.add(p,arcname=name)
print(json.dumps(summary),flush=True)
