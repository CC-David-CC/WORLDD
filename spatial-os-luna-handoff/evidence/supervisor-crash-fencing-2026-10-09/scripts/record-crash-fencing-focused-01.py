import ast
import json
from pathlib import Path
import re
import tarfile
r=Path(__file__).resolve().parents[1];e=r/'evidence/supervisor-crash-fencing-2026-10-09'
def write(p,v):p.write_bytes((json.dumps(v,indent=2)+'\n').encode())
profiles={}
for host in ('sb16','sb16-2'):
    with tarfile.open(r/('local/supervisor-crash-fencing-new-final-'+host+'-01.tar.gz')) as archive:
        assert all(p.isfile() and p.name.startswith('candidate-01/'+host+'/') for p in archive.getmembers())
        archive.extractall(e,filter='data')
    folder=e/'candidate-01'/host
    summary=json.loads((folder/'summary-new-final.json').read_text())
    assert summary['source_files']==294 and summary['locked_files']==41 and not summary['integrated']
    for profile in ('debug','runtime-sanitize'):
        result=summary[profile]
        assert tuple(result[k] for k in ('core_checks','core_suites','harness_checks','failed','skipped'))==(10,1,51,0,0)
        target=folder/(profile+'-new')
        value=(target/'fencing.log').read_text()
        assert re.findall(r'^\d+: Ran (\d+) tests? in ',value,re.M)==['10']
        assert '100% tests passed, 0 tests failed out of 1' in value
        assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',value)
        harness=(target/'harness.log').read_text()
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        profiles[host+'/'+profile]=dict(new_checks=10,harness_checks=51,failed=0,skipped=0)
write(e/'focused-results-review-01.json',dict(reviewer='primary agent',profiles=profiles,source_files=294,
    locked_files=41,production_changed=False,integrated=False,
    limits=['Full cumulative qualification is still running.','The bootstrap failure remains unresolved.',
        'Dependent control cleanup and verified issuer retirement remain required.']))
p=r/'tasks/m1/identity.supervisor_crash_fencing.json';value=json.loads(p.read_text())
value['qualification']='Both hosts passed10new checks and51harness checks in Debug and runtime ASan/UBSan. Full cumulative qualification continues.'
value['focused_review']='evidence/supervisor-crash-fencing-2026-10-09/focused-results-review-01.json';write(p,value)
heading='''## 2026-10-09: Automatic crash fencing passes both focused profiles

Both hosts passed ten new checks and 51 harness checks in Debug and runtime ASan/UBSan.
No focused check failed or skipped. Full cumulative qualification continues.
Read evidence/supervisor-crash-fencing-2026-10-09/focused-results-review-01.json.
The candidate remains unintegrated. Bootstrap authority-channel failures still require diagnosis.
Production remains broker283. M02 and Spatial-M1 remain incomplete.

'''
for p in (r/'CURRENT_STATE.md',r.parent/'M02_REVOCATION_BARRIER_DEBUG.txt'):
    value=p.read_text(encoding='utf-8');assert heading.splitlines()[0] not in value
    prefix='# Implementation status\n\n' if p.name=='CURRENT_STATE.md' else ''
    p.write_bytes((prefix+heading+value[len(prefix):]).encode())
p=r/'local/driver-state.json';value=json.loads(p.read_text())
value['last_turn']='progress: crash29410new+51harness passed all4profiles; supervisor293fullDebug passed bothhosts; issuer292sb16-2fullpassed,sb16runtime1bootstrapfailure preserved; owner-idle diagnostics running.'
write(p,value)
data=Path(__file__).read_bytes();ast.parse(data);(e/'scripts'/Path(__file__).name).write_bytes(data)
print('Reviewed automatic crash fencing in all four focused profiles. Full qualification remains required.')
