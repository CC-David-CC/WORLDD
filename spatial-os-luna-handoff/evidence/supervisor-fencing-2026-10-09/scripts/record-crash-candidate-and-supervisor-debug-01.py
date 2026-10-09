import ast
import hashlib
import json
from pathlib import Path
import re
import tarfile

r = Path(__file__).resolve().parents[1]
e = r/'evidence/supervisor-fencing-2026-10-09'
def write(path, value):
    path.write_bytes((json.dumps(value, indent=2)+'\n').encode())
hosts = {}
for host in ('sb16', 'sb16-2'):
    with tarfile.open(r/('local/supervisor-fencing-debug-'+host+'-01.tar.gz')) as archive:
        assert all(p.isfile() and p.name.startswith('candidate-01/'+host+'/') for p in archive.getmembers())
        archive.extractall(e, filter='data')
    target = e/'candidate-01'/host
    summary = json.loads((target/'summary-debug.json').read_text())
    assert summary['host'] == host and summary['source_files'] == 293 and summary['locked_files'] == 41
    result = summary['debug']
    assert tuple(result[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped')) == (1443,98,51,151,0,0)
    suites = []
    count = 0
    for name in ('fencing','focused','core'):
        value = (target/'debug'/(name+'.log')).read_text()
        numbers = list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',value,re.M)))
        names = re.findall(r'^\s*Start\s+\d+:\s+(\S+)',value,re.M)
        assert len(numbers) == len(names)
        assert f'100% tests passed, 0 tests failed out of {len(names)}' in value
        assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',value)
        count += sum(numbers)
        suites.extend(names)
    assert count == 1443 and len(suites) == len(set(suites)) == 98
    harness = (target/'debug/harness.log').read_text()
    assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    gate = json.loads((target/'debug/P10.json').read_text())
    assert gate['status'] == 'passed' and gate['executed'] == len(gate['tests']) == 151
    assert all(v['status'] == 'passed' for v in gate['tests'])
    hosts[host] = {k:result[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped')}
write(e/'full-debug-review-01.json', dict(reviewer='primary agent', hosts=hosts, production_changed=False,
    integrated=False, runtime_sanitizers='running', integration_dependency='Resolve the original world bootstrap failure.'))
for name in ('identity.supervisor_fencing','identity.supervisor_crash_fencing'):
    p=r/'tasks/m1'/(name+'.json'); value=json.loads(p.read_text())
    if name == 'identity.supervisor_fencing':
        value['qualification']='Both hosts passed full Debug:1443core,51harness,151P10. Full runtime sanitizers continue.'
        value['full_debug_review']='evidence/supervisor-fencing-2026-10-09/full-debug-review-01.json'
    else:
        value['implementation']='Frozen candidate01; unintegrated and unqualified.'
        value['candidate']=dict(number=1,source_files=294,changed_files=5,new_checks=10,locked_files_unchanged=41)
        value['live_drivers']={'sb16':1403725,'sb16_2':1196184}
        value['remaining']=['Complete focused and cumulative checks in both profiles on both hosts.',
            'Resolve the original bootstrap qualification failure before integration.',
            'Continue dependent control cleanup, issuer retirement, and cross-host cancellation.']
    write(p,value)
p=r/'docs/SUPERVISOR_CRASH_FENCING.txt'
value=p.read_text(encoding='utf-8')
value=value.replace('Status: Missing behavior reproduced. Implementation and qualification remain pending.',
    'Status: Missing behavior reproduced. Candidate01 is implemented. Qualification is running.\nProduction integration remains pending.')
p.write_bytes(value.encode())
heading='''## 2026-10-09: Full supervisor Debug passes; automatic crash fencing enters qualification

Both hosts passed supervisor fencing Debug: 1443 core checks, 51 harness checks, and 151 P10 cases.
All 98 core suites passed. No check failed or skipped. Full runtime sanitizers continue.
Read evidence/supervisor-fencing-2026-10-09/full-debug-review-01.json.

Automatic crash fencing candidate01 now contains 294 source files and ten new checks.
It requests the exact issuer fence on fatal service failure before existing checked cleanup.
It preserves the original fatal error and skips checked graceful release and explicit shutdown paths.
All existing tests, protobuf schemas, and 41 locked files remain unchanged.
Both hosts started qualification with two compiler jobs each.
This is unqualified candidate behavior. No production source is integrated.

The original bootstrap failure remains unresolved. Both 30-suite prefix diagnostics continue.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

'''
for p in (r/'CURRENT_STATE.md',r.parent/'M02_REVOCATION_BARRIER_DEBUG.txt'):
    value=p.read_text(encoding='utf-8')
    assert heading.splitlines()[0] not in value
    prefix='# Implementation status\n\n' if p.name=='CURRENT_STATE.md' else ''
    p.write_bytes((prefix+heading+value[len(prefix):]).encode())
p=r/'local/driver-state.json';value=json.loads(p.read_text())
value['last_turn']='progress: preserved320bootstrap diagnostic passes and8missing crash-fence failures; implemented294candidate; collected both full supervisor293Debug profiles; started294qualification.'
value['current_step']='bootstrap_failure_diagnosis_and_crash_fencing294_qualification'
value['qualification_sessions'].update(supervisor_crash_fencing_sb16_remote_pid=1403725,supervisor_crash_fencing_sb16_2_remote_pid=1196184)
value['next_test']='Poll bootstrap1365963/1153372, fencing1189236/979064, supervisor1266474/1047059, and crash-fencing1403725/1196184. Preserve any new failure before changing frozen candidates.'
write(p,value)
data=Path(__file__).read_bytes();ast.parse(data)
(e/'scripts'/Path(__file__).name).write_bytes(data)
print('Reviewed full supervisor Debug on both hosts; recorded unqualified crash-fencing candidate294.')
