"""Verify native replacement evidence and record this focused qualification."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert len(sys.argv) == 2 and len(sys.argv[1]) == 64
root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
archive = root / 'local/session-replacement-checks-03-sb16.tar.gz'
assert sha(archive.read_bytes()) == sys.argv[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'session-replacement-03/sb16'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({member.name for member in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name.startswith('session-replacement-03/sb16/')
        assert evidence.joinpath(*name.parts).resolve().is_relative_to(evidence.resolve())
    for member in members:
        path = evidence.joinpath(*PurePosixPath(member.name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
focused = json.loads((target / 'collected-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert run['status'] == 'passed' and focused['status'] == 'focused_passed_phase_gate_pending'
assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
assert focused['profiles']['debug']['replacement'] == [3]
assert len(focused['profiles']['debug']['focused']) == 7
assert all(sha((target / name).read_bytes()) == digest for name, digest in focused['attachments'].items())
integrated = {name: sha((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in run['source']}
assert integrated == run['source'] and len(integrated) == 343 and len(run['locked']) == 41
assert all(sha((root / name).read_bytes().replace(b'\r\n', b'\n')) == digest for name, digest in run['locked'].items())
previous = json.loads((evidence / 'stopped-broker-dependency-01/sb16/checks/review.json').read_bytes())
changed = {name for name in run['source'] if run['source'][name] != previous['source'].get(name)}
assert changed == {'CMakeLists.txt', 'src/testing/session_probe.proto', 'src/testing/session_application_probe.cpp',
    'tests/native/test_session_replacement.py'}, changed
failure = evidence / 'session-replacement-failure01/sb16'
failed = json.loads((failure / 'collected-review.json').read_bytes())
assert failed['status'] == 'preserved_failure'
assert all(sha((failure / name).read_bytes()) == digest for name, digest in failed['attachments'].items())
failure_run = json.loads((failure / 'checks/review.json').read_bytes())
assert sha((failure / 'test_session_replacement.py').read_bytes()) == failure_run['source']['tests/native/test_session_replacement.py']
log = (failure / 'checks/debug/replacement.log').read_text()
assert 'Ran 3 tests' in log and 'FAILED (failures=2)' in log
second_failure = evidence / 'session-replacement-failure02/sb16'
second = json.loads((second_failure / 'collected-review.json').read_bytes())
assert second['status'] == 'preserved_failure'
assert all(sha((second_failure / name).read_bytes()) == digest for name, digest in second['attachments'].items())
prior_bytes = (second_failure / 'checks/review.json').read_bytes()
prior = json.loads(prior_bytes)
assert sha(prior_bytes) == run['build_provenance_sha256']
assert sha((second_failure / 'test_session_replacement.py').read_bytes()) == prior['source']['tests/native/test_session_replacement.py']
assert {name for name in run['source'] if run['source'][name] != prior['source'][name]} == {'tests/native/test_session_replacement.py'}
for profile in ('debug', 'runtime-sanitize'):
    assert run['profiles'][profile]['reused_build']['binaries'] == prior['profiles'][profile]['binaries']
    assert prior['profiles'][profile]['configure']['exit_code'] == prior['profiles'][profile]['build']['exit_code'] == 0
log = (second_failure / 'checks/runtime-sanitize/replacement.log').read_text()
assert 'Ran 3 tests' in log and 'FAILED (errors=1)' in log and 'Issuer TLS stream closed' in log
count = focused['selected_cases_per_profile']
assert count == sum(sum(cases) for cases in focused['profiles']['debug'].values())
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    integrated_source=integrated, selected_cases_per_profile=count, new_cases=3, related_suites=7,
    failed=0, skipped=0, profiles=['Debug', 'runtime ASan/UBSan'], archive_sha256=sys.argv[1],
    attachments_verified=len(focused['attachments']), post_test_source_changes=0,
    production_launch_changed=False, limitations=focused['limitations'], compiled_input_changes_from_candidate02=0,
    reused_build_provenance=run['build_provenance_sha256'],
    preserved_failures=['../../session-replacement-failure01/sb16/failure-review.json',
        '../../session-replacement-failure02/sb16/failure-review.json'],
    file_review={
        'CMakeLists.txt': 'Registers three actual replacement scenarios in a delegated native test scope.',
        'src/testing/session_probe.proto': 'Adds typed private start and exact-owner call requests. Existing fields and production actor interfaces remain unchanged.',
        'src/testing/session_application_probe.cpp': 'Calls production start_world with private configuration and explicitly inherited native descriptors. Transfers a fresh actor channel by SCM_RIGHTS. Closes consumed descriptors. Old-owner calls still pass through production checks.',
        'tests/native/test_session_replacement.py': 'Reuses original world, workspace, project and policy paths. Verifies generation 2 activation, fresh credentials and launch identities, persistent actor selection and geometry, preserved uncommitted files, cleared held input, surviving application and new control lease. Old retirement retries leave new control intact. Unretired generations and revoked credentials are rejected.',
        'readiness correction': 'Candidate01 expected activation immediately after launch registration. Candidate02 waits on the actual issuer activation record, then retains all admission, persistence and generation assertions. The original failing test and logs remain preserved.',
        'administrator connection correction': 'Candidate02 outlived the fixture administrator connection idle timeout under sanitizers. Candidate03 opens a fresh authenticated connection for revocation. The timeout and all assertions remain unchanged.',
        'compiled input reuse': 'Only one Python test differs from candidate02. Both successful builds are reused after source and binary hash checks. CTest commands change only frozen test-source and evidence paths. Timeouts, native server, sanitizer settings, executable paths and test selection remain pinned.',
        'docs/SESSION_REPLACEMENT_RECOVERY.txt': 'Records the required distinction between fresh execution authority and preserved persistent identity. Keeps broker replacement, automatic surface restoration and the full gate pending.',
        'controller helpers': 'Freeze source and locked tests, run selected native profiles, preserve failure evidence, and verify exact collected and staged bytes.'
    })
write(target / 'primary-review.json', review)
status = root / 'CURRENT_STATE.md'; text = status.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert text.startswith(heading)
update = f'''
## 2026-10-10: Actual world replacement passes focused native checks

The existing production coordinator starts a fresh world generation after checked recovery and issuer retirement.
Native tests reuse the original world, workspace, project directory, and authority policy.
World identity, saved geometry, actor context, workspace selection, and uncommitted project data survive.
The application remains alive. Fresh actor authentication and a new control lease are required.
Old-owner routing is rejected. Retrying old retirement does not cancel replacement control.
Replacement is rejected before retirement. A previously revoked credential cannot enter the replacement.
The test driver changed. Production launch code did not change.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include three new native scenarios and seven related suites.
All 343 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/session-replacement-03/sb16/primary-review.json.
The first candidate observed asynchronous activation too early. Its two failures remain preserved.
The correction waits for the actual issuer record before checking admission and persistent state.
The second candidate's sanitizer run used an expired test administrator connection. That failure is also preserved.
The final fixture opens a fresh authenticated connection. Its idle timeout and assertions remain unchanged.
Compiled inputs match candidate02. Both profiles reused its verified binaries with separate commands and evidence paths.

Next: actual broker replacement, failure races, the earlier startup defect, and cross-host cancellation.
Full M02 qualification remains pending on both hosts.
Automatic application placement restoration and shared compositor lifetime remain required for M1.
No normal host session or database changed. M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
contract = root / 'docs/SESSION_REPLACEMENT_RECOVERY.txt'
text = contract.read_text(encoding='utf-8')
old = 'Status: Native qualification in progress. No replacement result is claimed yet.'
assert text.count(old) == 1
contract.write_bytes(text.replace(old,
    'Status: Focused Debug and sanitizer checks passed. Full M02 qualification remains pending.').encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'
task = json.loads(task_path.read_bytes())
task['current_step'] = 'actual_broker_replacement_and_failure_qualification'
task['remaining'] = ['Verify actual broker replacement and failure races.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Complete cross-host cancellation and full M02 qualification on both hosts.']
task['actual_world_replacement'] = dict(state='Actual world replacement passed focused native checks.',
    evidence='evidence/control-recovery-2026-10-09/session-replacement-03/sb16/primary-review.json',
    contract='docs/SESSION_REPLACEMENT_RECOVERY.txt', host='sb16', cases_per_profile=count, new_cases=3,
    profiles=review['profiles'], failed=0, skipped=0, full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail=f'Actual world replacement passed {count} cases per profile on sb16.')
driver['session_replacement_check'].update(status='passed_and_collected', session=None,
    evidence=task['actual_world_replacement']['evidence'], cases_per_profile=count, profiles=focused['profiles'])
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(f'''\n2026-10-10: Actual world replacement passed {count} cases per profile on sb16.
Three new scenarios and seven related native suites passed in Debug and runtime ASan/UBSan.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/session-replacement-03/sb16/primary-review.json.
The original activation-observation failures remain preserved in session-replacement-failure01/sb16.
The expired administrator-connection failure remains preserved in session-replacement-failure02/sb16.
Candidate03 reused both candidate02 builds. All compiled inputs and binary hashes were verified before and after testing.
Production launch code remains unchanged. Tests prove actual generation replacement with preserved world/workspace state.
The surviving application requires fresh actor authentication and a new lease. Old retirement does not cancel new control.
Broker replacement, failure races, the earlier startup defect, cross-host cancellation, and the full gate remain pending.
Automatic placement restoration and shared compositor lifetime remain required for M1.
No normal host session or database changed. M02 and Spatial-M1 remain incomplete.
'''.encode())
print(json.dumps(dict(source_files=343, selected_cases_per_profile=count,
    changed_source_files=sorted(changed), attachments=len(focused['attachments']))))
