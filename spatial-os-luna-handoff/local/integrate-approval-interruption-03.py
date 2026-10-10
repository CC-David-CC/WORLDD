"""Review focused interruption coverage without clearing the preserved phase-gate failures."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

root = Path(__file__).resolve().parents[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
archives = {'01': '092f48c1d6a0c4d0598851c6d3d4633908f393b4530a650bfff6888d27e0643b',
    '03': '7abf1e2c4f7ad9f4a468c0667df0cda034b431df70edc43bb3f10fa84e8a060e'}
for tag, digest in archives.items():
    archive = root / ('local/approval-interruption-checks-' + tag + '-sb16.tar.gz')
    assert sha(archive.read_bytes()) == digest
    prefix = 'approval-interruption-' + tag + '/sb16/'
    target = evidence / prefix
    assert not target.exists()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert len(members) == len({member.name for member in members})
        for member in members:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            assert member.name.startswith(prefix)
            assert evidence.joinpath(*name.parts).resolve().is_relative_to(evidence.resolve())
        for member in members:
            path = evidence.joinpath(*PurePosixPath(member.name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
    collected = json.loads((target / 'collected-review.json').read_bytes())
    run = json.loads((target / 'checks/review.json').read_bytes())
    assert run['status'] == 'passed'
    assert all(sha((target / name).read_bytes()) == digest for name, digest in collected['attachments'].items())
    assert sha((target / 'test_approval_interruption.py').read_bytes()) == run['source']['tests/native/test_approval_interruption.py']
    assert collected['selected_cases_per_profile'] == (4 if tag == '03' else 3)
    assert collected['profiles']['debug'] == collected['profiles']['runtime-sanitize']

integrated = {name: sha((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in run['source']}
assert integrated == run['source'] and len(integrated) == 345 and len(run['locked']) == 41
assert all(integrated[name] == digest for name, digest in run['locked'].items())
failed = json.loads((evidence / 'approval-interruption-failure02/sb16/checks/review.json').read_bytes())
assert {name for name in integrated if integrated[name] != failed['source'][name]} == {'tests/native/test_approval_interruption.py'}
for profile in ('debug', 'runtime-sanitize'):
    witnesses = list((target / 'checks' / profile / 'native').glob('*-witness.json.log'))
    assert len(witnesses) == 4
    for path in witnesses:
        value = json.loads(path.read_bytes())
        assert value['process_scope_stopped'] and value['native_scope_stopped']
        if value['injected_fault'] == 'committed_revocation_reply_withheld':
            assert value['generation'] == 1 and value['pending_count'] == 1 and value['applied_revision'] == 0
            assert value['fenced'] and not value['replacement_executed']
            assert value['participant_requests'].count('authority_activate') == 1
            assert 'authority_applied' not in value['participant_requests']
            assert 'authority_release' not in value['participant_requests']
        else:
            assert value['issuer_committed'] and value['issuer_generation'] == 0
            assert not value['local_authorization'] and not value['executed']
            assert value['supervisor_requests'].count('authority_launch_complete') == 1

timing = evidence / 'approval-timeout-timing-02'
timed = json.loads((timing / 'review.json').read_bytes())
assert timed['status'] == 'not_reproduced_cause_unconfirmed'
assert timed['test_exit_code'] == timed['trace_exit_code'] == 0
assert len(timed['results']) == 10 and all(result['passed'] and result['tests'] == 1 and not result['skipped'] for result in timed['results'])
assert all(sha((timing / name).read_bytes()) == digest for name, digest in timed['attachments'].items())
trace = (timing / 'timing.log').read_text()
assert 'ROOT_EXIT' in trace and 'TRACE_DEADLINE' not in trace and 'ERROR:' not in trace
assert not re.search(r'^(?:SLOW|WAIT) .*comm=identityd ', trace, re.M)
match = re.search(r'@maximum_us\[identityd, tracepoint:syscalls:sys_exit_fdatasync\]: (\d+)', trace)
assert match and int(match[1]) == 21357
write(timing / 'primary-review.json', dict(status=timed['status'], repetitions_passed=10,
    original_failure_resolved=False, source_and_deadlines_changed=False, issuer_max_fdatasync_us=21357,
    attachments_verified=len(timed['attachments']), kernel_trace_scope='Only the selected test process tree.',
    observations='No issuer syscall or off-CPU wait reached 100 ms in these successful repetitions.',
    limits='Successful repetitions do not determine the historical timeout cause. No TLS payloads or filesystem buffers were captured.'))
instrument_failure = evidence / 'approval-timeout-timing-failure01'
assert 'str() expects an integer or a pointer' in (instrument_failure / 'timing.log').read_text()
assert 'Timing tracer did not become ready' in (instrument_failure / 'run.log').read_text()
assert not (instrument_failure / 'results.json').exists()
write(instrument_failure / 'terminal-review.json', dict(status='instrumentation_compile_failure_no_cases_executed',
    detail='The first tracer used str() on an existing fixed string. The corrected tracer uses that string directly.',
    original_review_scope='The runner recorded its error before the waiting test driver reached its own startup deadline. That final run.log therefore differs from the early attachment hash.',
    final_attachments={p.relative_to(instrument_failure).as_posix(): sha(p.read_bytes()) for p in instrument_failure.rglob('*') if p.is_file()}))

review = dict(status='reviewed_focused_coverage_phase_gate_pending', reviewer='primary agent', host='sb16',
    selected_cases_per_profile=4, selected_failed=0, selected_skipped=0, profiles=['Debug', 'runtime ASan/UBSan'],
    integrated_source=integrated, locked_files_unchanged=41, production_code_changed=False,
    archive_sha256=archives['03'], attachments_verified=len(collected['attachments']),
    reused_builds='candidate02; every compiled input and binary hash matches before and after the checks',
    earlier_related_failure='Candidate02: Debug 52 passed; runtime sanitizers 51 passed, one active revocation-read timeout.',
    unresolved=['Original active-generation timeout cause.', 'Historical approval-startup failure cause.',
        'Remaining recovery races and cross-host cancellation.', 'Full M02 qualification on both hosts.'],
    file_review={
        'tests/native/test_approval_interruption.py': 'Four real-TLS fault scenarios use genuine issuer decisions and the production supervisor. They require exact owned cleanup and independent issuer/journal witnesses. The active-generation case preserves the pending obligation, rejects premature replacement, and proves no reconnect, acknowledgement, release, or replacement marker.',
        'CMakeLists.txt': 'Registers the interruption suite in the existing delegated native execution boundary with unchanged sanitizer options.',
        'docs/APPROVAL_INTERRUPTION_CHECKS.txt': 'Records invariants, timing requirements, selection, and unresolved evidence. No acceptance contract changes.',
        'local diagnostic and qualification helpers': 'Separate output paths preserve failed candidates. Hash checks pin frozen source, binaries, commands, and raw evidence. Timing probes select descendants of one test process and do not capture payloads.',
        'status and task records': 'Report controlled coverage separately from unresolved intermittent failures and the pending phase gate.'})
write(target / 'primary-review.json', review)
status_path = root / 'CURRENT_STATE.md'; status = status_path.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert status.startswith(heading)
update = '''
## 2026-10-10: Four controlled interruption scenarios pass focused checks

All four scenarios passed in Debug and runtime ASan/UBSan on sb16, without failures or skips.
Interrupted approval cannot start a child or reuse an abandoned incarnation.
A revocation-delivery timeout after activation fences the generation and stops its exact owned scopes.
The issuer keeps the revocation obligation pending. Replacement cannot execute before retirement.
No participant reconnect, acknowledgement, release, or replacement marker appears after that injected timeout.
Read evidence/control-recovery-2026-10-09/approval-interruption-03/sb16/primary-review.json.
All compiled inputs match candidate02. Both profiles reused its hash-verified binaries with separate evidence paths.
Only the new test changed after candidate02. All 41 locked files remain unchanged.

Candidate02's related intermittent timeout remains unresolved. Its failed run is preserved separately.
Five isolated and ten kernel-timed repetitions passed without changing code or deadlines.
The ten timed repetitions show no issuer syscall or off-CPU wait reaching 100 ms.
They do not determine the earlier failure's cause. The failed first tracer compilation is also preserved.
This is a development-branch coverage checkpoint, not full M02 qualification.
Next: cross-host cancellation and remaining recovery races. Both intermittent failures remain phase-gate work.
M02 and Spatial-M1 remain incomplete. No normal desktop session or database changed.

'''
status_path.write_bytes((heading + update + status[len(heading):]).encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = json.loads(task_path.read_bytes())
task['current_step'] = 'cross_host_cancellation_and_remaining_recovery_races'
task['approval_interruption'] = dict(state='Four controlled scenarios passed; earlier intermittent failures remain open.',
    evidence=str((target / 'primary-review.json').relative_to(root)).replace('\\', '/'),
    contract='docs/APPROVAL_INTERRUPTION_CHECKS.txt', cases_per_profile=4, host='sb16',
    full_phase_gate='pending on both hosts', original_timeout_resolved=False,
    preserved_failure='evidence/control-recovery-2026-10-09/approval-interruption-failure02/sb16/failure-review.json',
    timing_evidence='evidence/control-recovery-2026-10-09/approval-timeout-timing-02/primary-review.json')
if not any('active-generation' in item for item in task['remaining']):
    task['remaining'].append('Resolve the preserved active-generation revocation-read timeout before the full phase gate.')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail='Preserved the actual failure, collected bounded kernel timing, and verified active-generation timeout cleanup in both profiles.')
driver['approval_interruption_check'].update(status='reviewed_focused_coverage', session=None,
    terminal_session=69290, exit_code=0, cases_per_profile=4, evidence=task['approval_interruption']['evidence'],
    original_timeout_resolved=False)
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(('\n2026-10-10: Four controlled interruption scenarios passed per profile on sb16.\n'
        'Debug and runtime sanitizers reused exact candidate02 binaries. No compiled input changed.\n'
        'After active revocation-delivery timeout, exact scope cleanup and fencing preserve the pending issuer obligation.\n'
        'Replacement execution is rejected before retirement. No implicit reconnect or false acknowledgement occurs.\n'
        'Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/approval-interruption-03/sb16/primary-review.json.\n'
        'Ten additional kernel-timed repetitions passed. Maximum issuer fdatasync duration was 21.357 ms.\n'
        'These repetitions do not explain or resolve the original timeout. The first tracer compile error is preserved.\n'
        'Next: cross-host cancellation and remaining recovery races. Both intermittent failures remain phase-gate work.\n'
        'M02 and Spatial-M1 remain incomplete. No normal desktop session or database changed.\n').encode())
print(json.dumps(dict(status=review['status'], selected_cases_per_profile=4, source_files=len(integrated),
    original_timeout_resolved=False, attachments_verified=len(collected['attachments']))))
