import ast
import json
from pathlib import Path
import re
import tarfile
r=Path(__file__).resolve().parents[1]
b=r/'evidence/world-bootstrap-runtime-failure-2026-10-09'
e=r/'evidence/authority-fencing-2026-10-09'
def write(p,v):p.write_bytes((json.dumps(v,indent=2)+'\n').encode())
for name,target,prefix in [('fencing-bootstrap-failure-sb16-02.tar.gz',b,'failure-02/sb16/'),
                           ('authority-fencing-final-sb16-2-01.tar.gz',e,'candidate-01/sb16-2/')]:
    with tarfile.open(r/'local'/name) as archive:
        assert all(p.isfile() and p.name.startswith(prefix) for p in archive.getmembers())
        archive.extractall(target,filter='data')
failure=json.loads((b/'failure-02/sb16/summary.json').read_text())
assert (failure['core_checks'],failure['passed'],failure['failed'])==(1433,1432,1)
assert failure['P10']=='unrun_after_core_failure'
summary=json.loads((e/'candidate-01/sb16-2/summary-final.json').read_text())
for profile in ('debug','runtime-sanitize'):
    result=summary[profile]
    assert tuple(result[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped'))==(1433,97,51,151,0,0)
    folder=e/'candidate-01/sb16-2'/profile
    total=0
    for part in ('fencing','identity','core'):
        value=(folder/(part+'.log')).read_text()
        counts=list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',value,re.M)))
        total+=sum(counts)
        assert f'100% tests passed, 0 tests failed out of {len(counts)}' in value
    assert total==1433
    gate=json.loads((folder/'P10.json').read_text())
    assert gate['status']=='passed' and gate['executed']==151 and all(v['status']=='passed' for v in gate['tests'])
write(b/'failure-review-02.json',dict(reviewer='primary agent',failure=failure,cause='unresolved',
    relation_to_original='Same visible participant-channel failure. A common underlying cause is not yet proved.',
    distinct_evidence='Both broker and world report code26/request0 during the owner-idle scenario.',
    diagnostic=dict(source='Existing world290 diagnostic01 with exception-only logging.',
        limits='Not a qualification of fencing292. Relevant fixture, TLS server, and world session files match.',
        remote_pids={'sb16':1428761,'sb16-2':1225519},maximum_repetitions=20,
        unchanged=['Original test assertions','Original client and server timeouts','Authority behavior'])))
write(e/'full-results-review-02.json',dict(reviewer='primary agent',integrated=False,
    sb16_debug='1433core,51harness,151P10 passed',
    sb16_runtime='1432core passed,1bootstrap failed;51harness passed;P10 unrun',
    sb16_2='Full Debug and runtime ASan/UBSan passed:1433core,51harness,151P10 each.',
    integration_dependency='Diagnose and resolve the authority-channel failures before integration.'))
p=r/'tasks/m1/identity.authority_fencing.json';value=json.loads(p.read_text())
value['qualification']='sb16-2 passed full Debug and runtime ASan/UBSan. sb16 passed Debug; runtime core failed one owner-idle bootstrap check.'
value['full_review']='evidence/authority-fencing-2026-10-09/full-results-review-02.json'
value['integration_dependency']='Resolve bootstrap participant-channel failures; do not replace failed qualification with passing repetitions.'
write(p,value)
heading='''## 2026-10-09: Issuer fencing finishes with a second bootstrap failure

sb16-2 passed full issuer fencing in Debug and runtime ASan/UBSan.
Each profile passed 1433 core checks, 51 harness checks, and 151 P10 cases.
sb16 passed Debug. Its runtime core passed 1432 checks and failed one owner-idle bootstrap check.
Runtime P10 is unrun on sb16 because the driver stopped after that failure.
The failing world reported an unavailable participant channel. Its broker reported the same error class.
Both omitted the underlying exception. The cause remains unresolved.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/failure-review-02.json.

Both hosts now exercise this distinct idle scenario with the existing exception-logging diagnostic binaries.
These runs retain the original assertions and deadlines. They do not qualify the fencing candidate.
Automatic crash fencing passed ten new Debug checks per host; runtime builds continue.
Supervisor fencing full sanitizer runs continue. No new source is integrated.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

'''
for p in (r/'CURRENT_STATE.md',r.parent/'M02_REVOCATION_BARRIER_DEBUG.txt'):
    value=p.read_text(encoding='utf-8');assert heading.splitlines()[0] not in value
    prefix='# Implementation status\n\n' if p.name=='CURRENT_STATE.md' else ''
    p.write_bytes((prefix+heading+value[len(prefix):]).encode())
p=r/'local/driver-state.json';value=json.loads(p.read_text())
value['last_turn']='progress: crash294implemented+Debug10passed bothhosts; supervisor293fullDebug passed; issuer292final sb16-2passed and sb16failed one owner-idle bootstrap check, all evidence preserved; diagnostics running.'
value['qualification_sessions'].update(owner_idle_sb16_remote_pid=1428761,owner_idle_sb16_2_remote_pid=1225519)
value['next_test']='Issuer292 drivers are terminal. Poll supervisor1266474/1047059, crash2941403725/1196184, and owner-idle1428761/1225519. Inspect diagnostic exceptions before proposing a transport fix.'
write(p,value)
for name in ('preserve-fencing-bootstrap-failure-02.py','diagnose-owner-idle-04.py','start-owner-idle-04.py','record-fencing-bootstrap-failure-02.py'):
    data=(r/'local'/name).read_bytes();ast.parse(data);(b/'scripts'/name).write_bytes(data)
print('Preserved sb16 issuer failure and verified sb16-2 full final result; production unchanged.')
