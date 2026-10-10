"""Import immutable diagnostic evidence and record qualified versus failed results."""
from pathlib import Path
import hashlib
import json
import re
import tarfile

r=Path(__file__).resolve().parents[1]
e=r/'evidence/world-bootstrap-runtime-failure-2026-10-09'
def write(path,value):path.write_bytes((json.dumps(value,indent=2)+'\n').encode())
for host in ('sb16','sb16-2'):
    for name in (f'build-overlap-diagnostic-{host}-07.tar.gz',f'authority-suite-diagnostic-{host}-08.tar.gz'):
        with tarfile.open(r/'local'/name) as archive:archive.extractall(e,filter='data')
    for number in ('04','05'):
        folder=e/('failure-'+number)/host
        summary=json.loads((folder/'summary.json').read_text())
        for name,digest in summary['files'].items():assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest

review=dict(reviewer='primary agent',production_changed=False,integration_allowed=False,profiles={})
for host in ('sb16','sb16-2'):
    for number,label in (('04','supervisor293/runtime-sanitize'),('05','crash294/debug')):
        value=json.loads((e/('failure-'+number)/host/'summary.json').read_text())
        review['profiles'][host+'/'+label]={k:value[k] for k in ('core_checks','failed_test_methods','passed_test_methods','failed_suites','P10')}
review['limits']=[
    'These terminal failed runs do not qualify either candidate.',
    'Crash294 focused Debug and runtime sanitizer checks remain passing; full runtime qualification is unrun.',
    'The underlying exception is absent from these original failures. Do not claim an individually proven cause for each.',
    'No original failure record is replaced by diagnostic repetitions.']
write(e/'full-failure-review-04.json',review)

diagnostics={}
for host in ('sb16','sb16-2'):
    records=json.loads((e/'transport-diagnostic-05'/host/'run.json').read_text())['results']
    assert len(records)==13 and all(x['exit_code']==0 for x in records)
    folder=e/'transport-diagnostic-05'/host
    counts=[]
    for record in records:
        text=(folder/record['name']/'test.log').read_text()
        counts.append(int(re.search(r'Ran (\d+) tests? in ',text).group(1)))
        assert text.rstrip().endswith('OK')
    assert counts==[3]+[1,15]*6
    records=json.loads((e/'pixel-teardown-diagnostic-06'/host/'repetitions.json').read_text())
    assert len(records)==11 and all(x['exit_code']==0 for x in records)
    for index in range(11):
        text=(e/'pixel-teardown-diagnostic-06'/host/('repeat-'+str(index+1).zfill(2))/'test.log').read_text()
        assert int(re.search(r'Ran (\d+) tests? in ',text).group(1))==(1 if index<10 else 14)
        assert text.rstrip().endswith('OK')
    build=json.loads((e/'build-overlap-07'/host/'summary.json').read_text())
    assert len(build['tests'])==30 and all(x['exit_code']==0 for x in build['tests']) and build['build_exit_code']==0
    assert all(x['build_live_at_start'] for x in build['tests'][:24])
    assert all(not x['build_live_at_start'] for x in build['tests'][24:])
    authority=json.loads((e/'authority-suite-08'/host/'runs.json').read_text())
    assert [x['exit_code'] for x in authority]==([0,0,0] if host=='sb16' else [1])
    diagnostics[host]=dict(control_checks=3,bootstrap_checks=96,pixel_checks=24,
        build_overlap_checks=24,after_build_checks=6,
        workspace_authority_suites=authority)

folder=e/'authority-suite-08/sb16-2/suite-1/service-logs'
prefix='WorkspaceAuthorityTests-test_cross_domain_unknown_and_foreign_grants_are_rejected-'
broker=(folder/(prefix+'broker-1.log')).read_text()
server=(folder/(prefix+'tls-0.log')).read_text()
assert 'reason=Issuer client deadline exceeded' in broker and 'local_port=50352' in broker
assert 'loop_gap_ms=881' in server and 'peer_port=50352' in server
write(e/'diagnostic-results-review-08.json',dict(reviewer='primary agent',diagnostic_only=True,
    production_changed=False,profiles=diagnostics,
    confirmed='The instrumented workspace authority suite lost its participant channel after the 700 ms client deadline. The issuer recorded an 881 ms event-loop gap.',
    supporting='A two-job compiler workload was active. IO pressure increased near the recorded stall. CPU pressure remained near zero.',
    limits=[
        'The exact blocking syscall is not captured in diagnostic08.',
        'This does not individually prove the cause of every original failure.',
        'The single-method build-overlap repetitions all passed. Suite-level reproduction was required.',
        'Passing diagnostics do not qualify production or remove failed original results.'],
    next='Trace only issuer fsync/fdatasync to distinguish durable storage latency from other event-loop work.'))

for name in ('preserve-supervisor-failures-04.py','preserve-crash-fencing-failure-05.py',
             'diagnose-pixel-teardown-06.py','start-pixel-teardown-06.py',
             'diagnose-build-overlap-07.py','start-build-overlap-07.py',
             'diagnose-authority-suite-08.py','start-authority-suite-08.py',
             'diagnose-issuer-sync-09.py','start-issuer-sync-09.py','record-transport-results-08.py'):
    (e/'scripts'/name).write_bytes((r/'local'/name).read_bytes())

for name,qualification in (
    ('identity.supervisor_fencing.json','Full Debug passed on both hosts. Full runtime sanitizers failed two test methods on sb16 and three on sb16-2. Runtime P10 is unrun.'),
    ('identity.supervisor_crash_fencing.json','Focused checks passed on both hosts in both profiles. Full Debug failed one test method on each host. Full P10 and runtime cumulative checks are unrun.')):
    path=r/'tasks/m1'/name;value=json.loads(path.read_text())
    value['qualification']=qualification
    value['terminal_drivers']=value.pop('live_drivers',{})
    value['full_failure_review']='evidence/world-bootstrap-runtime-failure-2026-10-09/full-failure-review-04.json'
    write(path,value)

path=r/'local/driver-state.json';state=json.loads(path.read_text())
state['previous_goal_turn']='verified wait: inspected live crash-fencing processes and completed supervisor failures.'
state['last_turn']='progress: preserved terminal293/294 failures and reproduced a classified 700 ms authority timeout with an 881 ms issuer event-loop gap.'
state['current_step']='diagnose_issuer_durability_stall_before_resuming_full_qualification'
state['transport_diagnostic08']='evidence/world-bootstrap-runtime-failure-2026-10-09/diagnostic-results-review-08.json'
state['issuer_sync_diagnostic09']=dict(sb16_pid=1535982,sb16_2_pid=1326720,diagnostic_only=True,
    limitation='Ptrace disables LeakSanitizer only in the traced issuer. This run is not qualification.')
state['integration_hold']='Original failures retained. Diagnose the reproduced event-loop stall and complete required cumulative qualification.'
write(path,state)

entry='''## 2026-10-09: Authority timeout reproduced with an issuer event-loop stall

The instrumented workspace-authority suite failed on sb16-2 during a two-job build.
The issuer recorded an 881 ms event-loop gap. The client deadline was 700 ms.
The client disabled the participant channel after that deadline. IO pressure increased near the stall.
The exact blocking syscall remains unknown. A scoped fsync/fdatasync trace is running on both hosts.
The trace disables LeakSanitizer only in the traced issuer because ptrace prevents that detector.
This trace is diagnostic evidence. It cannot qualify sanitizer behavior.

Supervisor293 full sanitizers finished with two failed test methods on sb16 and three on sb16-2.
Crash294 full Debug finished with one failed test method on each host.
Crash294 full runtime qualification and both candidates' affected P10 profiles remain unrun.
Their focused results remain passing. Original failure logs remain preserved.

Both hosts passed three diagnostic controls, 96 bootstrap checks, and 24 pixel checks.
Both also passed 24 single-method checks during compilation and six after compilation.
The full workspace-authority suite reproduced the stall despite those individual passes.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/diagnostic-results-review-08.json.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/full-failure-review-04.json.

Production remains broker283. No candidate is integrated. M02 and Spatial-M1 remain incomplete.

'''
path=r/'CURRENT_STATE.md';text=path.read_text(encoding='utf-8')
assert text.startswith('# Implementation status\n\n')
path.write_bytes((text[:len('# Implementation status\n\n')]+entry+text[len('# Implementation status\n\n'):]).encode())
path=r.parent/'M02_REVOCATION_BARRIER_DEBUG.txt'
with path.open('ab') as stream:stream.write(('\n2026-10-09: Classified issuer stall\n'+
    'An instrumented workspace-authority run hit its 700 ms client deadline during an 881 ms issuer event-loop gap.\n'+
    'A two-job build was active. IO pressure increased. The exact blocking syscall remains under investigation.\n'+
    'Supervisor293 full sanitizers and crash294 full Debug contain terminal failures. No production integration is permitted yet.\n'+
    'Evidence: spatial-os-luna-handoff/evidence/world-bootstrap-runtime-failure-2026-10-09/diagnostic-results-review-08.json\n'+
    'Issuer-only fsync/fdatasync tracing is diagnostic. Its leak detector is disabled under ptrace; it is not a sanitizer pass.\n').encode())
print('Reviewed failed qualification and classified diagnostic evidence. Production remains unchanged.')
