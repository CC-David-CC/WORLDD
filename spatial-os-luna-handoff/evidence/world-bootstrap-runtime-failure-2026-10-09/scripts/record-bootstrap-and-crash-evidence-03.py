"""Import and review completed diagnostics without changing production source."""
import ast
import hashlib
import json
from pathlib import Path
import re
import tarfile

r = Path(__file__).resolve().parents[1]
bootstrap = r/'evidence/world-bootstrap-runtime-failure-2026-10-09'
crash = r/'evidence/supervisor-crash-fencing-2026-10-09'
crash.mkdir(exist_ok=True)
def write(path, value):
    path.write_bytes((json.dumps(value, indent=2)+'\n').encode())
def extract(archive, target, prefix):
    with tarfile.open(archive) as bundle:
        assert all(p.name.startswith(prefix+'/') and p.isfile() for p in bundle.getmembers())
        bundle.extractall(target, filter='data')
for host in ('sb16', 'sb16-2'):
    extract(r/('local/world-bootstrap-suite-diagnostic-'+host+'-02.tar.gz'), bootstrap, 'suite-diagnostic-02/'+host)
    extract(r/('local/supervisor-crash-fencing-baseline-'+host+'-01.tar.gz'), crash, 'baseline-01/'+host)
diagnostics = {}
for host in ('sb16', 'sb16-2'):
    entries = {}
    for folder, count, checks, stem in [('diagnostic-01', 40, 1, 'repeat'), ('suite-diagnostic-02', 8, 15, 'suite')]:
        target = bootstrap/folder/host
        records = json.loads((target/'repetitions.json').read_text())
        assert len(records) == count and all(v['exit_code'] == 0 for v in records)
        hashes = {}
        for index in range(1, count+1):
            log = target/(stem+'-'+str(index).zfill(2))/'test.log'
            value = log.read_text()
            assert re.findall(r'^Ran (\d+) tests? in ', value, re.M) == [str(checks)]
            assert value.rstrip().endswith('OK')
            assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL', value)
            hashes[log.relative_to(bootstrap).as_posix()] = hashlib.sha256(log.read_bytes()).hexdigest()
        entries[folder] = dict(repetitions=count, checks_per_repetition=checks, failed=0, test_log_hashes=hashes)
    diagnostics[host] = entries
write(bootstrap/'diagnostic-results-review-03.json', dict(reviewer='primary agent', diagnostic_only=True,
    qualification=False, hosts=diagnostics, original_failure_unresolved=True, production_changed=False,
    conclusion='Neither isolated repetition nor the original bootstrap suite order reproduced the failure.',
    next_diagnostic='Original 30-suite prefix through world-bootstrap on unchanged diagnostic01 binaries.',
    prefix_remote_pids={'sb16':1365963,'sb16-2':1153372},
    source_review=[
        'scoped_world closes its preliminary world before constructing the tested world.',
        'The tested world retains one synchronous issuer client and one participant worker connection.',
        'The fixture retains the owner connection. Registration uses a temporary connection closed in finally.',
        'These paths do not establish connection-limit exhaustion. No runtime evidence proves that hypothesis.',
        'The original code26/request0 diagnostic omits the underlying exception. Cause remains unknown.',
        'No timeout, retry, assertion, or authority behavior changed in these diagnostic runs.']))
profiles = {}
for host in ('sb16', 'sb16-2'):
    summary = json.loads((crash/'baseline-01'/host/'summary.json').read_text())
    assert summary['source_files'] == 293 and not summary['production_changed'] and not summary['qualification']
    for profile, entry in summary['profiles'].items():
        assert entry['expected_missing_feature'] and not entry['sanitizer_marker']
        log = crash/'baseline-01'/host/profile/'test.log'
        value = log.read_text()
        assert re.findall(r'^Ran (\d+) tests? in ', value, re.M) == ['2']
        assert 'FAILED (failures=2)' in value
        assert value.count('AssertionError: False is not true : Missing automatic supervisor crash fence') == 2
        profiles[host+'/'+profile] = dict(expected_missing_feature_failures=2,
            test_log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
assert len(profiles) == 4
write(crash/'baseline-review-01.json', dict(reviewer='primary agent', profiles=profiles,
    acceptance_pass=False, source_files=293, production_changed=False,
    finding='Child death and management loss complete checked local cleanup but do not persist an issuer fence.',
    preserved_before_failed_assertion=['Original nonzero supervisor failure status','Exact process and native cleanup receipts',
        'Unchanged issuer activation history','One pending revocation obligation'],
    next_step='Implement bounded automatic crash fencing with preserved error and cleanup behavior.'))
for folder, names in [(bootstrap, ['diagnose-bootstrap-suite-02.py','start-bootstrap-suite-02.py',
        'diagnose-bootstrap-prefix-03.py','start-bootstrap-prefix-03.py','record-bootstrap-and-crash-evidence-03.py']),
        (crash, ['test_supervisor_crash_fencing_baseline.py','diagnose-supervisor-crash-fencing-01.py'])]:
    (folder/'scripts').mkdir(exist_ok=True)
    for name in names:
        data = (r/'local'/name).read_bytes()
        ast.parse(data)
        (folder/'scripts'/name).write_bytes(data)
write(r/'tasks/m1/identity.supervisor_crash_fencing.json', dict(id='identity.supervisor_crash_fencing',
    phase='M02', parent='identity.supervisor_recovery', status='in_progress',
    contract='docs/SUPERVISOR_CRASH_FENCING.txt', evidence='evidence/supervisor-crash-fencing-2026-10-09',
    production_changed=False, implementation='pending',
    baseline='Two expected missing-feature failures in each of four host/profile combinations.',
    review='Primary agent. Existing tests and production sources unchanged.',
    remaining=['Implement automatic crash fencing through retained launch ownership.',
               'Preserve original errors, checked cleanup, and unresolved issuer obligations.',
               'Run focused and full qualification on both hosts and both profiles.']))
heading = '''## 2026-10-09: Bootstrap failure remains unresolved; automatic crash fencing gap reproduced

Both hosts passed 40 isolated repetitions of the failing bootstrap method.
Both hosts also passed eight complete bootstrap-suite repetitions, with 15 checks per repetition.
These diagnostic passes do not replace the original failed qualification.
The original startup failure remains unresolved. Production integration remains on hold.
Both hosts now run the original 30-suite sequence through world-bootstrap with diagnostic-only binaries.
No assertions, deadlines, retries, or authority behavior changed.
Read evidence/world-bootstrap-runtime-failure-2026-10-09/diagnostic-results-review-03.json.

Two new crash witnesses fail as expected on each host in Debug and runtime ASan/UBSan.
The production supervisor performs checked cleanup after child death and management loss.
It preserves pending issuer obligations but does not request the required automatic fence.
Read docs/SUPERVISOR_CRASH_FENCING.txt and evidence/supervisor-crash-fencing-2026-10-09.
Automatic crash fencing implementation remains pending.

Issuer fencing full sanitizer runs and supervisor fencing cumulative runs remain active.
Production remains broker283. No new candidate is integrated. M02 and Spatial-M1 remain incomplete.

'''
p = r/'CURRENT_STATE.md'
current = p.read_text(encoding='utf-8')
assert heading.splitlines()[0] not in current
assert current.startswith('# Implementation status\n\n')
p.write_bytes((current[:len('# Implementation status\n\n')]+heading+current[len('# Implementation status\n\n'):]).encode())
p = r.parent/'M02_REVOCATION_BARRIER_DEBUG.txt'
p.write_bytes((heading+p.read_text(encoding='utf-8')).encode())
p = r/'local/driver-state.json'
state = json.loads(p.read_text())
state['previous_goal_turn'] = 'verified wait: polled exact live issuer, supervisor, and bootstrap suite diagnostic processes.'
state['last_turn'] = 'progress: verified80isolated+240suite diagnostic checks; reproduced8missing automatic crash-fence failures; started original30suite diagnostic prefix on both hosts.'
state['current_step'] = 'bootstrap_failure_diagnosis_and_automatic_crash_fencing_baseline'
state['next_test'] = 'Inspect prefix diagnostic1365963/1153372 and active fencing qualifiers1189236/979064/1266474/1047059. Implement crash fencing after baseline review.'
state['qualification_sessions'].update(bootstrap_prefix_sb16_remote_pid=1365963, bootstrap_prefix_sb16_2_remote_pid=1153372)
write(p, state)
print('Reviewed 320 passing diagnostic checks and eight expected missing-feature failures. No integration claim.')
