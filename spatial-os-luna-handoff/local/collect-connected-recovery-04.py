"""Collect final world registration and checked terminal delivery evidence."""
import hashlib,json,re,socket,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
root=r/'local/m02-connected-recovery-04';run=json.loads((root/'checks/review.json').read_bytes())
assert run['status']=='passed'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
files={}
for profile in ('debug','runtime-sanitize'):
    value=run['profiles'][profile]
    for name in ('configure','build','world-registration','focused'):
        entry=value[name];path=root/'checks'/profile/(name+'.log')
        assert entry['exit_code']==0 and sha(path)==entry['log_sha256']
    for name,digest in value['binaries'].items():assert sha(root/profile/'build'/name)==digest
    for name,counts,suites in [('world-registration',[6],1),('focused',[14,6,13,11,8,4,15],7)]:
        text=(root/'checks'/profile/(name+'.log')).read_text()
        assert not any(x in text for x in ('... skipped','skipped=','ERROR: AddressSanitizer','ERROR: LeakSanitizer','AddressSanitizer:DEADLYSIGNAL','runtime error:','FAILED ('))
        assert list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)))==counts
        assert f'100% tests passed, 0 tests failed out of {suites}' in text
    cache=(root/profile/'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL='+('ON' if profile=='runtime-sanitize' else 'OFF') in cache
for group in ('source','locked'):
    assert all(sha(root/'source'/n)==h for n,h in run[group].items())
assert sha(Path(run['server']['path']))==run['server']['sha256']
for path in (root/'checks').rglob('*'):
    if path.is_file() and (path.suffix=='.log' or path.name=='review.json'):
        data=path.read_bytes();assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
        files[path.relative_to(root).as_posix()]=sha(path)
review=dict(status='focused_passed_phase_gate_pending',host='sb16',source_files=len(run['source']),locked_files=len(run['locked']),
    selected_cases_per_profile=77,new_world_cases=6,related_cases=71,profiles=['debug','runtime-sanitize'],failed=0,skipped=0,
    attachments=files,baseline='world-crash-baseline01 preserved one failure with completed cleanup and a surviving broker.',
    verified=['Exact retained world owner and registered broker endpoint','No private coordinator socket inherited by world or compositor',
        'Checked normal world shutdown preserves broker','Checked final receipt after world and supervisor exit',
        'Supervisor death without a receipt cannot establish cleanup'],
    missing=['World actor transport admission','Native application survival and selective reservation recovery',
        'Durable dependent receipt and issuer retirement','Shared compositor lifetime across worlds','Full M02 and M1 qualification on both hosts'],
    prior_unresolved='One connected01 participant-approval startup failure remains unexplained despite subsequent passing cases.')
summary=root/'focused-review.json'
with summary.open('x') as f:json.dump(review,f,indent=2);f.write('\n')
archive=r/'local/connected-recovery-focused-04-sb16.tar.gz'
with tarfile.open(archive,'x:gz') as t:
    for name in files:t.add(root/name,arcname='connected-04/sb16/'+name,recursive=False)
    t.add(summary,arcname='connected-04/sb16/focused-review.json',recursive=False)
print(json.dumps(dict(archive=str(archive),sha256=sha(archive),attachments=len(files),checks_per_profile=77)))
