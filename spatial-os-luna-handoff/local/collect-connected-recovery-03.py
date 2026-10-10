"""Collect world registration and independent observation evidence after both runs stop."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
root=r/'local/m02-connected-recovery-03'
run=json.loads((root/'checks/review.json').read_bytes())
observation=json.loads((root/'checks-observation06/review.json').read_bytes())
assert run['status']==observation['status']=='passed'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(root/'checks/review.json')==observation['original_review_sha256']
assert sha(r/'local/test_session_world_06.py')==observation['fixture_sha256']
files={}
def clean(text):
    assert not any(x in text for x in ('... skipped','skipped=','ERROR: AddressSanitizer','ERROR: LeakSanitizer',
        'AddressSanitizer:DEADLYSIGNAL','runtime error:','FAILED ('))
for profile in ('debug','runtime-sanitize'):
    value=run['profiles'][profile]
    for name in ('configure','build','world-registration','focused'):
        entry=value[name]; path=root/'checks'/profile/(name+'.log')
        assert entry['exit_code']==0 and sha(path)==entry['log_sha256']
    for name,digest in value['binaries'].items():assert sha(root/profile/'build'/name)==digest
    world=(root/'checks'/profile/'world-registration.log').read_text();clean(world)
    assert re.findall(r'^\d+: Ran (\d+) tests? in ',world,re.M)==['4']
    assert '100% tests passed, 0 tests failed out of 1' in world
    related=(root/'checks'/profile/'focused.log').read_text();clean(related)
    assert list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',related,re.M)))==[14,6,13,11,8,4,15]
    assert '100% tests passed, 0 tests failed out of 7' in related
    detail=observation['results'][profile];path=root/'checks-observation06'/(profile+'.log')
    assert detail['exit_code']==0 and sha(path)==detail['log_sha256']
    text=path.read_text();clean(text)
    assert re.findall(r'^Ran (\d+) tests? in ',text,re.M)==['4'] and text.count(' ... ok')==4 and '\nOK\n' in text
    cache=(root/profile/'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL='+('ON' if profile=='runtime-sanitize' else 'OFF') in cache
for group in ('source','locked'):
    assert all(sha(root/'source'/name)==digest for name,digest in run[group].items())
assert sha(Path(run['server']['path']))==run['server']['sha256']
for folder in ('checks','checks-observation06'):
    for path in (root/folder).rglob('*'):
        if path.is_file() and (path.suffix=='.log' or path.name=='review.json'):
            data=path.read_bytes();assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
            files[path.relative_to(root).as_posix()]=sha(path)
review=dict(status='focused_passed_phase_gate_pending',host='sb16',source_files=len(run['source']),locked_files=len(run['locked']),
    new_world_registration_cases=4,related_cases=71,selected_cases_per_profile=75,
    profiles=['debug','runtime-sanitize'],failed=0,skipped=0,
    extra_observation_rerun_cases_per_profile=4,
    independent_observations=['Saved world launch record equals the coordinator snapshot.',
        'Live worldd and spatialwm descriptor inventories share no private coordinator socket.',
        'The broker answers after checked world shutdown.'],
    corrected_fixture_sha256=observation['fixture_sha256'],production_changed_after_first_run=False,attachments=files,
    missing=['World actor transport admission','Checked cleanup after asynchronous world or supervisor failure',
        'Native application survival and selective reservation recovery','Durable dependent receipt and issuer retirement',
        'Shared compositor lifetime across multiple worlds','Complete M02 and Spatial-M1 qualification on both hosts'],
    prior_unresolved='connected01 participant approval startup failed once; later passing repetitions do not explain it.')
summary=root/'focused-review.json'
with summary.open('x') as f:json.dump(review,f,indent=2);f.write('\n')
archive=r/'local/connected-recovery-focused-03-sb16.tar.gz'
with tarfile.open(archive,'x:gz') as t:
    for name in files:t.add(root/name,arcname='connected-03/sb16/'+name,recursive=False)
    t.add(summary,arcname='connected-03/sb16/focused-review.json',recursive=False)
print(json.dumps(dict(archive=str(archive),sha256=sha(archive),attachments=len(files),checks_per_profile=75)))
