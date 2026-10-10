"""Verify tested stopped-broker recovery before recording branch integration."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert len(sys.argv) == 2 and len(sys.argv[1]) == 64
root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda p, v: p.write_bytes((json.dumps(v, indent=2) + '\n').encode())
archive = root / 'local/stopped-broker-dependency-focused-01-sb16.tar.gz'
assert sha(archive.read_bytes()) == sys.argv[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'stopped-broker-dependency-01/sb16'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({m.name for m in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name.startswith('stopped-broker-dependency-01/sb16/')
        assert evidence.joinpath(*name.parts).resolve().is_relative_to(evidence.resolve())
    for member in members:
        path = evidence.joinpath(*PurePosixPath(member.name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(tar.extractfile(member).read())

focused = json.loads((target / 'focused-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert run['status'] == 'passed' and focused['failed'] == focused['skipped'] == 0
assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
assert focused['profiles']['debug']['stopped-broker-dependency'] == [9]
assert len(focused['profiles']['debug']['focused']) == 8
assert all(sha((target / n).read_bytes()) == h for n, h in focused['attachments'].items())
integrated = {n: sha((root / n).read_bytes().replace(b'\r\n', b'\n')) for n in run['source']}
assert integrated == run['source'] and len(integrated) == 342 and len(run['locked']) == 41
assert all(sha((root / n).read_bytes().replace(b'\r\n', b'\n')) == h for n, h in run['locked'].items())
baseline = evidence / 'stopped-broker-dependency-baseline01/sb16'
record = json.loads((baseline / 'baseline-review.json').read_bytes())
assert record['status'] == 'expected_missing_step_failure'
assert all(sha((baseline / n).read_bytes()) == h for n, h in record['attachments'].items())
log = (baseline / 'baseline.log').read_text()
assert 'Ran 1 test' in log and 'FAILED (failures=1)' in log
assert 'World dependency recovery is missing after checked broker cleanup' in log
count = focused['selected_cases_per_profile']
assert count == sum(sum(c) for c in focused['profiles']['debug'].values())
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    integrated_source=integrated, selected_cases_per_profile=count, new_cases=9, failed=0, skipped=0,
    profiles=['Debug', 'runtime ASan/UBSan'], archive_sha256=sys.argv[1],
    attachments_verified=len(focused['attachments']), post_test_source_changes=0,
    limitations=focused['limitations'], baseline=focused['baseline'], file_review={
        'CMakeLists.txt': 'Registers nine native cases using the production runtime, existing native fixture, and a delegated systemd scope.',
        'proto/spatial/supervisor/v1/launch.proto': 'Documents dependency receipt versions 1 and 2. Existing protobuf field numbers remain unchanged.',
        'src/supervisor/launch.cpp': 'Version 2 requires exact checked stopped world and broker scopes, common issuer/server, distinct launches, and absent controls. Version 1 retains its control recovery validation. Both use the same immutable durable journal writer and reject changed original launch bytes. Existing world retirement binds the saved dependency bytes.',
        'src/supervisor/launch.hpp': 'Keeps receipt construction and persistence private to retained production ownership.',
        'src/supervisor/session_broker.cpp': 'Returns checked cleanup only from the original owning process and retained successful stop.',
        'src/supervisor/session_broker.hpp': 'Exposes the immutable retained cleanup receipt separately from issuer retirement.',
        'src/supervisor/session_coordinator.cpp': 'Recovers the exact retained world after checked broker cleanup. Retirement requires retained ownership and durable dependency proof without requiring broker liveness. Failed receipt replacement preserves the original proof.',
        'src/supervisor/session_coordinator.hpp': 'Declares explicit stopped-broker recovery with only the retained world owner as input.',
        'src/testing/session_probe.proto': 'Adds private test request 19 and typed dependent result 15. Actor interfaces remain unchanged.',
        'src/testing/session_application_probe.cpp': 'Forwards the new test request directly to the production coordinator.',
        'tests/native/test_stopped_broker_dependency.py': 'Nine cases use actual crashes and independent journal/issuer observations. Cover both proof versions, absent receipts, substituted ownership, corrupted bytes, write failures, exact retries, and denied execution after reopening.',
        'docs/STOPPED_BROKER_DEPENDENCIES.txt': 'Defines version 2 separately from reservation-release receipts, with explicit ownership, persistence, and retirement requirements.',
        'related documentation': 'Distinguishes live-broker control recovery from stopped-broker scope recovery and keeps replacement and phase qualification pending.',
        'controller helpers': 'Preserve the failing baseline, freeze source, run selected checks, collect hashed evidence, and verify exact working and staged source.'
    })
write(target / 'primary-review.json', review)

status = root / 'CURRENT_STATE.md'
text = status.read_text(encoding='utf-8')
heading = '# Implementation status\n'
assert text.startswith(heading)
update = f'''
## 2026-10-10: World dependency recovery after broker shutdown passes focused checks

The retained coordinator now recovers a stopped world's dependency after checked broker shutdown.
Receipt version 2 binds both exact checked scope records. It does not invent a control-channel receipt.
The original world launch bytes and revision remain unchanged during the explicit journal migration.
World retirement remains available after broker exit through retained ownership and either durable proof version.
Existing version 1 receipts remain immutable. Broker and world retirement remain separate operations.
Neither receipt authorizes replacement execution or reconstructs lost process ownership.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include nine new native cases and eight related suites.
All 342 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/stopped-broker-dependency-01/sb16/primary-review.json.
The actual missing-operation baseline remains preserved. No normal host session or database changed.

Next: actual replacement recovery and failure races.
The earlier participant-approval startup failure, cross-host cancellation, and full two-host qualification remain required work.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())

def replace_text(name, old, new):
    path = root / name
    text = path.read_text(encoding='utf-8')
    assert text.count(old) == 1, (name, old)
    path.write_bytes(text.replace(old, new).encode())

replace_text('docs/STOPPED_BROKER_DEPENDENCIES.txt',
    'Status: Implementation contract. Focused checks remain pending.',
    'Status: Focused Debug and sanitizer checks passed. Full M02 qualification remains pending.')
replace_text('docs/DEPENDENT_CLEANUP_PERSISTENCE.txt',
    'The receipt binds one stopped world to the exact retained broker that released its reservations.',
    'The receipt binds one stopped world to its exact retained broker and checked dependency cleanup.')
replace_text('docs/DEPENDENT_CLEANUP_PERSISTENCE.txt',
    'Schema version 1 records one complete immutable result.',
    'Schema version 1 records live-broker control recovery. Schema version 2 records stopped-broker scope cleanup.\nBoth versions record one complete immutable result.')
replace_text('docs/DEPENDENT_CLEANUP_PERSISTENCE.txt',
    'Controls must contain that exact owner and those exact process and native cleanup receipts.',
    'Version 1 controls must contain that exact owner and those exact process and native cleanup receipts.')
replace_text('docs/DEPENDENT_CLEANUP_PERSISTENCE.txt',
    'Broker must match the retained, live SessionBroker launch snapshot.',
    'For version 1, broker must match the retained, live SessionBroker launch snapshot.')
replace_text('docs/DEPENDENT_CLEANUP_PERSISTENCE.txt',
    'Journal ownership and migration\n',
    'Version 2 instead requires exact checked broker process and native cleanup.\n'
    'It must not contain controls. It does not claim a reservation-release reply.\n'
    'Read STOPPED_BROKER_DEPENDENCIES.txt for its complete validation and retirement contract.\n'
    'The following journal rules apply to both receipt versions.\n\nJournal ownership and migration\n')
replace_text('docs/DEPENDENT_CLEANUP_PERSISTENCE.txt',
    'The broker may release reservations before the journal write fails.',
    'For version 1, the broker may release reservations before the journal write fails.')
replace_text('docs/DEPENDENT_CLEANUP_PERSISTENCE.txt',
    'Do not repeat external cleanup or infer success from an empty reservation list.',
    'Do not repeat external cleanup or infer success from an empty reservation list.\n'
    'For version 2, retry with both original checked scope records and retained coordinator ownership.')
replace_text('docs/AUTHORITY_RETIREMENT.txt',
    'Dependent world recovery with a stopped broker remains pending.',
    'World retirement accepts durable live-broker control recovery or checked stopped-broker scope recovery.\n'
    'Read STOPPED_BROKER_DEPENDENCIES.txt for the two distinct proof versions.')
replace_text('docs/BROKER_RETIREMENT.txt',
    'World dependency recovery with a stopped broker remains a separate required operation.',
    'World dependency recovery is a separate explicit operation. Read STOPPED_BROKER_DEPENDENCIES.txt.')

task_path = root / 'tasks/m1/identity.control_recovery.json'
task = json.loads(task_path.read_bytes())
task['current_step'] = 'actual_replacement_recovery_and_failure_qualification'
task['remaining'] = ['Verify actual replacement recovery and failure races.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Complete cross-host cancellation and full M02 qualification on both hosts.']
task['stopped_broker_dependency'] = dict(state='Checked stopped-broker dependency recovery passed focused checks.',
    evidence='evidence/control-recovery-2026-10-09/stopped-broker-dependency-01/sb16/primary-review.json',
    contract='docs/STOPPED_BROKER_DEPENDENCIES.txt', host='sb16', cases_per_profile=count, new_cases=9,
    profiles=review['profiles'], failed=0, skipped=0, full_phase_gate='pending on both hosts')
task['session_broker']['state'] = 'Broker lifecycle foundation passed focused checks. Later sections record connected world coordination and recovery.'
task['connected_application']['state'] = 'Focused native application recovery passed. Selective reservation recovery is recorded separately below.'
task['selective_recovery']['state'] = 'Focused selective reservation recovery passed. Durable receipts and actual retirement are recorded separately below.'
task['dependent_cleanup']['state'] = 'Version 1 durable control-recovery receipts passed focused checks. Version 2 scope receipts are recorded under stopped_broker_dependency.'
task['issuer_retirement']['state'] = 'Issuer and client protocol passed focused checks. Actual coordinator and broker integration are recorded separately below.'
write(task_path, task)
driver_path = root / 'local/driver-state.json'
driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress',
    detail=f'Stopped-broker dependency recovery passed {count} cases per profile on sb16. M02 remains incomplete.')
driver['stopped_broker_dependency_check'].update(status='passed_and_collected',
    evidence=task['stopped_broker_dependency']['evidence'], cases_per_profile=count, profiles=focused['profiles'])
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(f'''\n2026-10-10: Stopped-broker world dependency recovery passed {count} cases per profile on sb16.
Nine new native cases and eight related suites passed in Debug and runtime ASan/UBSan.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/stopped-broker-dependency-01/sb16/primary-review.json.
The original missing-operation baseline remains preserved in stopped-broker-dependency-baseline01/sb16.
Version 2 records exact checked scope cleanup. Version 1 control-recovery receipts remain valid and immutable.
No control reply is invented. Separate world retirement remains available after broker exit.
Actual replacement recovery, failure races, cross-host cancellation, and full two-host qualification remain pending.
The earlier participant-approval startup failure remains unresolved.
No normal session or database changed. M02 and Spatial-M1 remain incomplete.
'''.encode())
print(json.dumps(dict(source_files=342, selected_cases_per_profile=count, attachments=len(focused['attachments']))))
