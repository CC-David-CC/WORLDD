"""Correlate traced durability calls with the reproduced issuer loop gaps."""
import hashlib
import json
from pathlib import Path
import re

r=Path(__file__).resolve().parents[1];e=r/'evidence/world-bootstrap-runtime-failure-2026-10-09'
def write(path,value):path.write_bytes((json.dumps(value,indent=2)+'\n').encode())
results={}
for host in ('sb16','sb16-2'):
    root=e/'issuer-sync-09'/host
    summary=json.loads((root/'summary.json').read_text())
    assert summary['diagnostic_only'] and not summary['qualification'] and summary['build_exit_code']==0
    assert [x['exit_code'] for x in summary['tests']]==([0,0,0,0] if host=='sb16' else [0,1,0])
    assert not summary['tests'][-1]['build_live_at_start']
    results[host]=dict(build_exit=0,suite_exit_codes=[x['exit_code'] for x in summary['tests']],
        after_build_suite_passed=True,source_manifest=summary['source_files'])
    for index,record in enumerate(summary['tests'],1):
        name=str(index).zfill(2)+'-'+record['phase']+'-workspace-authority'
        text=(root/name/'test.log').read_text()
        assert 'Ran 20 tests' in text
        if record['exit_code']==0:assert text.rstrip().endswith('OK')
    results[host]['artifact_hashes']={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file()}

root=e/'issuer-sync-09/sb16-2'
samples=[json.loads(line) for line in (root/'resource-samples.jsonl').read_text().splitlines()]
folder=root/'02-build-overlap-workspace-authority/service-logs'
groups=[('1330388',1791590479.20,1791590480.241,1035,251675980,
         'test_file_scope_does_not_authorize_a_whole_project_mount'),
        ('1331816',1791590526.80,1791590527.589,800,251723327,
         'test_workspace_policy_limits_a_broader_actor_grant')]
correlations=[]
for pid,start,end,gap,server_time,method in groups:
    path=folder/('issuer-sync.'+pid)
    calls=[]
    for line in path.read_text().splitlines():
        match=re.match(r'([0-9.]+) fdatasync\((\d+)\)\s+= 0 <([0-9.]+)>$',line)
        if match and start<=float(match[1])<=end:
            calls.append(dict(start=float(match[1]),fd=int(match[2]),duration=float(match[3])))
    assert len(calls)==5
    total=sum(x['duration'] for x in calls)
    assert total>0.7
    sample=min(samples,key=lambda x:abs(x['monotonic']-server_time/1000))
    offset=sample['time']-sample['monotonic']
    syscall_end=calls[-1]['start']+calls[-1]['duration']-offset
    assert abs(syscall_end-server_time/1000)<.01
    assert sample['build_live']
    prefix='WorkspaceAuthorityTests-'+method+'-'
    server=(folder/(prefix+'tls-0.log')).read_text()
    broker=(folder/(prefix+'broker-1.log')).read_text()
    assert f'loop_gap_ms={gap}' in server and 'Issuer client deadline exceeded' in broker
    correlations.append(dict(method=method,fdatasync_calls=calls,total_sync_seconds=total,
        server_loop_gap_ms=gap,server_time_ms=server_time,syscall_end_monotonic=syscall_end,
        clock_sample=sample['time'],compiler_live_at_sample=True))

write(e/'issuer-sync-review-09.json',dict(reviewer='primary agent',diagnostic_only=True,
    production_changed=False,qualification=False,profiles=results,correlations=correlations,
    conclusion='Synchronous durability calls stalled the issuer long enough to exceed the 700 ms fixture deadline during concurrent compilation. The participant then failed closed.',
    limits=[
        'Ptrace affects timing; diagnostic08 reproduced an 881 ms stall without ptrace.',
        'LeakSanitizer was disabled only in the traced issuer. These are not sanitizer qualification passes.',
        'Original uninstrumented failures remain preserved and are not each individually proven to have this cause.',
        'No production deadline, durability setting, authority rule, or test assertion changed.'],
    decision='Run one cumulative qualification job per host after compilers finish. Keep both hosts active in parallel.',
    next='Full frozen294 Debug and runtime sanitizer qualification in local/crash-fencing-serial-02.'))
for name in ('qualify-crash-fencing-serial-02.py','start-crash-fencing-serial-02.py','record-issuer-sync-09.py'):
    (e/'scripts'/name).write_bytes((r/'local'/name).read_bytes())
entry='''## 2026-10-09: Durable sync latency identified; full qualification restarted without compiler overlap

The issuer trace reproduced participant timeouts during a two-job build on sb16-2.
Five consecutive fdatasync calls took 1.021265 seconds. The matching event-loop gap was 1035 ms.
A second sequence took 0.783983 seconds, matching an 800 ms gap.
Both exceeded the fixture client's unchanged 700 ms deadline. The participants failed closed.
The same full workspace-authority suite passed after compilation finished.
The trace affects timing. Diagnostic08 already reproduced an 881 ms gap without ptrace.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/issuer-sync-review-09.json.

Both hosts now run full qualification of the unchanged frozen294 candidate.
Each host runs one qualifier after its diagnostic build and tests finish.
The qualifier checks for compiler overlap and preserves new results separately.
Debug and runtime ASan/LeakSanitizer/UBSan assertions, deadlines, and durability settings are unchanged.
Read docs/QUALIFICATION_SCHEDULING.txt. These runs are pending, not passing evidence.

Original failed runs remain preserved. Production remains broker283 until full qualification and integration review succeed.
M02 and Spatial-M1 remain incomplete.

'''
path=r/'CURRENT_STATE.md';text=path.read_text(encoding='utf-8');header='# Implementation status\n\n'
assert text.startswith(header);path.write_bytes((header+entry+text[len(header):]).encode())
path=r/'docs/MILESTONES.md';text=path.read_text(encoding='utf-8')
text=text.replace('The blocking operation remains under investigation. No candidate qualification or integration is inferred from diagnostic passes.',
    'A syscall trace identified consecutive durable sync calls exceeding that deadline during compilation.\nFull unchanged qualification now runs without compiler overlap. Diagnostic passes do not establish candidate qualification.')
path.write_bytes(text.encode())
path=r/'local/driver-state.json';state=json.loads(path.read_text())
state['last_turn']='progress: traced repeated fdatasync stalls matching issuer loop delays; started full frozen294 qualification without compiler overlap.'
state['current_step']='full_frozen294_qualification_without_compiler_overlap'
state['issuer_sync_diagnostic09']['status']='terminal; reproduced sync latency on sb16-2; after-build suites passed both hosts'
state['serial_qualification02']=dict(sb16_pid=1544923,sb16_2_pid=1334189,status='running',
    source_files=294,production_changed=False,rule='Do not start compiler workloads on either host while these qualifiers run.')
state['integration_hold']='Require full frozen294 qualification and primary integration review. Historical failures remain retained.'
write(path,state)
path=r/'tasks/m1/identity.supervisor_crash_fencing.json';task=json.loads(path.read_text())
task['qualification_rerun']=dict(status='running',output='local/crash-fencing-serial-02',
    sb16_pid=1544923,sb16_2_pid=1334189,source_unchanged=True,assertions_and_deadlines_unchanged=True)
task['diagnosis']='evidence/world-bootstrap-runtime-failure-2026-10-09/issuer-sync-review-09.json'
write(path,task)
path=r.parent/'M02_REVOCATION_BARRIER_DEBUG.txt'
with path.open('ab') as stream:stream.write(('\n2026-10-09: Durable sync latency captured\n'+
    'Five issuer fdatasync calls took 1.021265 seconds during compilation and matched a 1035 ms event-loop gap.\n'+
    'A second sequence took 0.783983 seconds and matched an 800 ms gap. The unchanged client deadline is 700 ms.\n'+
    'The full authority suite passed after compilation. Original failures remain preserved.\n'+
    'Both hosts now qualify frozen294 without compiler overlap. All sanitizer options, assertions, and deadlines remain unchanged.\n'+
    'Do not start another compiler workload while qualifier PIDs1544923(sb16) and1334189(sb16-2) are active.\n'+
    'Evidence: spatial-os-luna-handoff/evidence/world-bootstrap-runtime-failure-2026-10-09/issuer-sync-review-09.json\n').encode())
print('Correlated both sync stalls and recorded unchanged full qualification rerun.')
