import ast
import hashlib
import json
from pathlib import Path
import re
import tarfile
r=Path(__file__).resolve().parents[1];e=r/'evidence/world-bootstrap-runtime-failure-2026-10-09'
def write(p,v):p.write_bytes((json.dumps(v,indent=2)+'\n').encode())
hosts={}
for host in ('sb16','sb16-2'):
    with tarfile.open(r/('local/owner-idle-diagnostic-'+host+'-04.tar.gz')) as archive:
        assert all(p.isfile() and p.name.startswith('owner-idle-diagnostic-04/'+host+'/') for p in archive.getmembers())
        archive.extractall(e,filter='data')
    folder=e/'owner-idle-diagnostic-04'/host
    records=json.loads((folder/'repetitions.json').read_text())
    assert len(records)==20 and all(v['exit_code']==0 for v in records)
    hashes={}
    for index in range(1,21):
        p=folder/('repeat-'+str(index).zfill(2))/'test.log';value=p.read_text()
        assert re.findall(r'^Ran (\d+) tests? in ',value,re.M)==['1'] and value.rstrip().endswith('OK')
        assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',value)
        hashes[str(index)]=hashlib.sha256(p.read_bytes()).hexdigest()
    hosts[host]=dict(repetitions=20,failed=0,test_log_hashes=hashes)
write(e/'owner-idle-results-review-04.json',dict(reviewer='primary agent',hosts=hosts,diagnostic_only=True,
    qualification=False,original_failures_unresolved=True,
    conclusion='The distinct owner-idle scenario did not reproduce under existing exception-only diagnostic binaries.'))
manifest=json.loads((r/'local/transport-diagnostic-05.json').read_text())
assert len(manifest['files'])==294 and len(manifest['changed'])==3
write(e/'transport-diagnostic-review-05.json',dict(reviewer='primary agent',status='building',
    source_files=294,changed_files=manifest['changed'],tests_and_schemas_unchanged=True,
    deadlines_and_authorization_unchanged=True,production_changed=False,
    controls=['Pause only the fixture-owned issuer; expect a client deadline and fail-closed world.',
              'Stop only the fixture-owned issuer; expect a TLS close and fail-closed world.',
              'Send an incomplete frame; expect server header expiry with one byte consumed.'],
    limitations=['Logging can affect timing.','Control faults do not establish the cause of earlier failures.',
                 'No test, retry, deadline, or authorization relaxation is included.'],
    remote_pids={'sb16':1460060,'sb16-2':1255950},
    references=['https://docs.openssl.org/3.0/man3/SSL_pending/'],
    source_review='TLS error values are captured before diagnostic OpenSSL calls. No payload or credential is logged.'))
for name in ('prepare-transport-diagnostic-05.py','test_transport_diagnostic_controls_05.py',
             'diagnose-transport-05.py','start-transport-diagnostic-05.py','record-transport-diagnostic-start-05.py'):
    data=(r/'local'/name).read_bytes();ast.parse(data);(e/'scripts'/name).write_bytes(data)
heading='''## 2026-10-09: Transport diagnostics now record the missing failure context

Both hosts completed 20 owner-idle repetitions with no failure. The original failures remain unresolved.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/owner-idle-results-review-04.json.

A separate diagnostic build now records client operation, I/O stage, deadline lag, and TLS errors.
It also records server peer state, close timing, connection-limit events, and event-loop delay.
No payloads or credentials are logged. Production source, assertions, deadlines, and retries remain unchanged.
Three controlled faults will verify the diagnostics before the original failing scenarios run.
These controls do not establish the cause of the original failures.
Both hosts started the diagnostic build with two compiler jobs each.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/transport-diagnostic-review-05.json.

Supervisor and automatic crash-fencing full qualification continue on both hosts.
Integration remains on hold. Production remains broker283. M02 and Spatial-M1 remain incomplete.

'''
for p in (r/'CURRENT_STATE.md',r.parent/'M02_REVOCATION_BARRIER_DEBUG.txt'):
    value=p.read_text(encoding='utf-8');assert heading.splitlines()[0] not in value
    prefix='# Implementation status\n\n' if p.name=='CURRENT_STATE.md' else ''
    p.write_bytes((prefix+heading+value[len(prefix):]).encode())
p=r/'local/driver-state.json';value=json.loads(p.read_text())
value['previous_goal_turn']='progress: implemented crash294 and verified all four focused profiles; preserved second bootstrap failure and full sb16-2 issuer results.'
value['last_turn']='progress: preserved40owner-idle diagnostic passes; prepared passive three-file diagnostics with three real transport fault controls; started builds on both hosts.'
value['qualification_sessions'].update(transport_diagnostic_sb16_remote_pid=1460060,transport_diagnostic_sb16_2_remote_pid=1255950)
value['next_test']='Poll diagnostics1460060/1255950, supervisor1266474/1047059, crash2941403725/1196184. Owner-idle1428761/1225519 are terminal20pass each. Verify controls before interpreting new failure traces.'
write(p,value)
print('Preserved40owner-idle passes and reviewed passive transport diagnostics. No integration claim.')
