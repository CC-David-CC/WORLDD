import ast
import hashlib
import json
from pathlib import Path
import re
import tarfile
r=Path(__file__).resolve().parents[1]
bootstrap=r/'evidence/world-bootstrap-runtime-failure-2026-10-09'
crash=r/'evidence/supervisor-crash-fencing-2026-10-09'
def write(p,value):p.write_bytes((json.dumps(value,indent=2)+'\n').encode())
prefixes={};debug={}
for host in ('sb16','sb16-2'):
    for name,target,prefix in [('world-bootstrap-prefix-diagnostic-'+host+'-03.tar.gz',bootstrap,'prefix-diagnostic-03/'+host+'/'),
                               ('supervisor-crash-fencing-new-debug-'+host+'-01.tar.gz',crash,'candidate-01/'+host+'/')]:
        with tarfile.open(r/'local'/name) as archive:
            assert all(p.isfile() and p.name.startswith(prefix) for p in archive.getmembers())
            archive.extractall(target,filter='data')
    folder=bootstrap/'prefix-diagnostic-03'/host
    result=json.loads((folder/'result.json').read_text())
    assert result['exit_code']==0 and result['diagnostic_only'] and not result['qualification']
    value=(folder/'core.log').read_text()
    suites=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',value,re.M)
    counts=list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',value,re.M)))
    assert suites==result['suites'] and len(suites)==len(counts)==30 and suites[-1]=='world-bootstrap'
    assert '100% tests passed, 0 tests failed out of 30' in value
    assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',value)
    prefixes[host]=dict(suites=30,checks=sum(counts),failed=0,qualification=False,
        core_log_sha256=hashlib.sha256((folder/'core.log').read_bytes()).hexdigest())
    folder=crash/'candidate-01'/host
    summary=json.loads((folder/'summary-new-debug.json').read_text())
    assert summary['source_files']==294 and summary['locked_files']==41 and not summary['integrated']
    result=summary['debug']
    assert tuple(result[k] for k in ('core_checks','core_suites','harness_checks','failed','skipped'))==(10,1,51,0,0)
    value=(folder/'debug-new/fencing.log').read_text()
    assert re.findall(r'^\d+: Ran (\d+) tests? in ',value,re.M)==['10']
    assert '100% tests passed, 0 tests failed out of 1' in value
    debug[host]=dict(new_checks=10,harness_checks=51,failed=0,skipped=0)
write(bootstrap/'prefix-results-review-03.json',dict(reviewer='primary agent',hosts=prefixes,
    original_failure_unresolved=True,production_changed=False,
    conclusion='The original preceding suite order also did not reproduce the authority-channel failure.',
    next_diagnostic='Capture classified transport failure and server peer-expiry context before any further blind repetition.'))
write(crash/'debug-results-review-01.json',dict(reviewer='primary agent',hosts=debug,
    source_files=294,locked_files=41,production_changed=False,integrated=False,
    remaining=['Runtime ASan/UBSan focused checks.','Full cumulative qualification on both hosts.',
               'Resolve bootstrap integration dependency.','Dependent control cleanup and verified issuer retirement.']))
p=r/'tasks/m1/identity.supervisor_crash_fencing.json';value=json.loads(p.read_text())
value['qualification']='Both hosts passed10new Debug checks and51harness checks. Related regressions, sanitizers, and cumulative qualification continue.'
value['debug_review']='evidence/supervisor-crash-fencing-2026-10-09/debug-results-review-01.json'
write(p,value)
heading='''## 2026-10-09: Automatic crash fencing passes focused Debug on both hosts

Both hosts passed ten new automatic crash-fencing checks and 51 harness checks in Debug.
The tests cover child death, management loss, termination, issuer loss, signing keys, and uncertain cleanup.
They preserve pending revocation obligations and distinguish committed fences from completed recovery.
Read evidence/supervisor-crash-fencing-2026-10-09/debug-results-review-01.json.
Related regressions, runtime sanitizers, and full cumulative qualification continue.
The 294-file candidate remains unintegrated.

Both hosts completed the original 30-suite bootstrap diagnostic prefix without failures.
The original startup failure remains unresolved. These diagnostic passes do not replace its failed qualification.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/prefix-results-review-03.json.
Further diagnosis needs transport and server-expiry context. Do not rely on more blind repetitions.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

'''
for p in (r/'CURRENT_STATE.md',r.parent/'M02_REVOCATION_BARRIER_DEBUG.txt'):
    value=p.read_text(encoding='utf-8');assert heading.splitlines()[0] not in value
    prefix='# Implementation status\n\n' if p.name=='CURRENT_STATE.md' else ''
    p.write_bytes((prefix+heading+value[len(prefix):]).encode())
p=r/'local/driver-state.json';value=json.loads(p.read_text())
value['last_turn']='progress: automatic crash fencing294 passed10new Debug checks+51harness on both hosts; both original30suite diagnostic prefixes passed without explaining original failure; evidence preserved.'
value['next_test']='Poll fencing1189236/979064, supervisor1266474/1047059, crash-fencing1403725/1196184. Bootstrap prefixes1365963/1153372 are terminal; add classified transport/expiry context before another diagnostic run.'
write(p,value)
data=Path(__file__).read_bytes();ast.parse(data);(crash/'scripts'/Path(__file__).name).write_bytes(data)
print(json.dumps(dict(bootstrap_prefix=prefixes,crash_debug=debug)))
