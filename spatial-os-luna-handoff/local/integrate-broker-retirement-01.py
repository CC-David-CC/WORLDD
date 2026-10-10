"""Verify exact tested broker retirement source before recording integration."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert len(sys.argv) == 2 and len(sys.argv[1]) == 64
root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda p, v: p.write_bytes((json.dumps(v, indent=2) + '\n').encode())
archive = root / 'local/broker-retirement-focused-01-sb16.tar.gz'
assert sha(archive.read_bytes()) == sys.argv[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'broker-retirement-01/sb16'; assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers(); assert len(members) == len({m.name for m in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name.startswith('broker-retirement-01/sb16/')
    for member in members:
        path = evidence.joinpath(*PurePosixPath(member.name).parts)
        assert path.resolve().is_relative_to(evidence.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
focused = json.loads((target / 'focused-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert run['status'] == 'passed' and focused['failed'] == focused['skipped'] == 0
assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
assert focused['profiles']['debug']['broker-retirement'] == [9]
assert len(focused['profiles']['debug']['focused']) == 9
assert all(sha((target / n).read_bytes()) == h for n, h in focused['attachments'].items())
integrated = {n: sha((root / n).read_bytes().replace(b'\r\n', b'\n')) for n in run['source']}
assert integrated == run['source'] and len(integrated) == 341 and len(run['locked']) == 41
assert all(sha((root / n).read_bytes().replace(b'\r\n', b'\n')) == h for n, h in run['locked'].items())
baseline = evidence / 'broker-retirement-baseline01/sb16'
record = json.loads((baseline / 'baseline-review.json').read_bytes())
assert record['status'] == 'expected_missing_step_failure'
assert all(sha((baseline / n).read_bytes()) == h for n, h in record['attachments'].items())
log = (baseline / 'baseline.log').read_text()
assert 'Ran 1 test' in log and 'FAILED (failures=1)' in log
assert 'Broker retirement is missing after actual checked cleanup' in log
count = focused['selected_cases_per_profile']
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    integrated_source=integrated, selected_cases_per_profile=count, new_cases=9, failed=0, skipped=0,
    profiles=['Debug', 'runtime ASan/UBSan'], archive_sha256=sys.argv[1], attachments_verified=len(focused['attachments']),
    post_test_source_changes=0, limitations=focused['limitations'], baseline=focused['baseline'],
    file_review={
        'CMakeLists.txt': 'Registers nine actual broker retirement scenarios in a delegated scope, using the production runtime and existing native fixture.',
        'src/supervisor/launch.cpp': 'Preserves raw launch bytes. Reads explicit broker journal schema 4 while retaining world schemas 2 and 3. Requires exact checked BROKER cleanup and fence. Shares the existing authenticated issuer exchange without changing its binding checks. Saves broker retirement in a schema 1-to-4 transaction after validating the issuer result. Exact retry retains the receipt and cannot reconstruct process ownership.',
        'src/supervisor/launch.hpp': 'Grants only the trusted retained SessionBroker access to the private broker retirement method.',
        'src/supervisor/session_broker.cpp': 'Retains original journal and signing configuration, checked cleanup and issuer result. Requires creator ownership and checked stop. Rejects any changed cleanup or cached retirement.',
        'src/supervisor/session_broker.hpp': 'Declares the trusted broker retirement operation with a typed protobuf result and no caller-supplied proof material.',
        'src/supervisor/session_coordinator.cpp': 'Forwards retirement through retained coordinator ownership after broker exit.',
        'src/supervisor/session_coordinator.hpp': 'Exposes broker retirement separately from broker and world cleanup.',
        'src/testing/session_probe.proto': 'Adds private test operation 18. Existing fields and actor interfaces stay unchanged.',
        'src/testing/session_application_probe.cpp': 'Forwards the test operation directly to production coordinator retirement.',
        'tests/native/test_broker_retirement.py': 'Nine scenarios verify actual crash cleanup, issuer and local state, exact digest and result, unaffected generations, rejected contexts, issuer/local write failures, durable retry, supervisor loss, and denied execution after journal reopening.',
        'docs/BROKER_RETIREMENT.txt': 'Records the explicit schema 4 broker layout and exact proof, durability, ownership and retry contract.',
        'docs/AUTHORITY_RETIREMENT.txt': 'Distinguishes world dependent-cleanup evidence from broker scope-cleanup evidence.',
        'controller helpers': 'Preserve the missing-operation baseline, freeze reviewed source, run selected profiles, collect raw evidence, and verify exact working and staged source.'
    })
write(target / 'primary-review.json', review)
status = root / 'CURRENT_STATE.md'; text = status.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert text.startswith(heading)
update = f'''
## 2026-10-10: Broker retirement after checked cleanup passes focused checks

The retained coordinator now retires a failed broker generation after checked process and native cleanup.
Its original supervisor signer binds the exact saved launch bytes and committed issuer fence.
The issuer retires only that broker generation. Independent generations retain their existing state.
Explicit launch journal schema 4 saves the issuer result without changing the cleanup bytes or revision.
Exact retries preserve the original result, including after an issuer commit followed by local storage failure.
Broker retirement does not replace world cleanup or authorize replacement execution.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include nine new native scenarios and nine related suites.
All 341 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/broker-retirement-01/sb16/primary-review.json.
The actual missing-retirement baseline remains preserved. No normal host session or database changed.

Next: dependent world recovery with a stopped broker, then actual replacement recovery and failure races.
The earlier participant-approval startup failure, cross-host cancellation, and full two-host qualification remain required work.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
contract = root / 'docs/BROKER_RETIREMENT.txt'; text = contract.read_text(encoding='utf-8')
old = 'Status: Implementation contract. Focused checks remain pending.'; assert text.count(old) == 1
contract.write_bytes(text.replace(old, 'Status: Focused Debug and sanitizer checks passed. Full M02 qualification remains pending.').encode())
contract = root / 'docs/AUTHORITY_RETIREMENT.txt'; text = contract.read_text(encoding='utf-8')
old = 'Bind the proof to the durable dependent-cleanup record through its SHA-256 digest.'; assert text.count(old) == 1
text = text.replace(old, 'Bind the proof to the exact durable cleanup bytes through their SHA-256 digest.\nWorld retirement uses its dependent-cleanup record. Broker retirement uses its checked stopped launch record.\nRead BROKER_RETIREMENT.txt for the explicit broker journal layout.')
old = 'Connect the trusted coordinator signer in the next increment and require actual cleanup before signing.'; assert text.count(old) == 1
text = text.replace(old, 'Retained world and broker signers require actual checked cleanup before signing.\nDependent world recovery with a stopped broker remains pending.')
contract.write_bytes(text.encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = json.loads(task_path.read_bytes())
task['current_step'] = 'stopped_broker_dependencies_and_replacement_recovery'
task['remaining'] = ['Complete dependent world recovery with a stopped broker.',
    'Verify actual replacement recovery and failure races.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Complete cross-host cancellation and full M02 qualification on both hosts.']
task['broker_retirement'] = dict(state='Actual checked broker cleanup-to-retirement passed focused checks.',
    evidence='evidence/control-recovery-2026-10-09/broker-retirement-01/sb16/primary-review.json',
    contract='docs/BROKER_RETIREMENT.txt', host='sb16', cases_per_profile=count, new_cases=9,
    profiles=review['profiles'], failed=0, skipped=0, full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail=f'Broker retirement passed {count} cases per profile on sb16. M02 remains incomplete.')
driver['broker_retirement_check'].update(status='passed_and_collected', evidence=task['broker_retirement']['evidence'],
    cases_per_profile=count, profiles=focused['profiles'])
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(f'''\n2026-10-10: Broker retirement passed {count} cases per profile on sb16.
Nine new native scenarios and nine related suites passed in Debug and runtime ASan/UBSan.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/broker-retirement-01/sb16/primary-review.json.
The original missing-operation baseline remains preserved in broker-retirement-baseline01/sb16.
Only the broker generation is retired. Its exact cleanup bytes and independently authorized generations remain unchanged.
Dependent world recovery with a stopped broker, replacement recovery, failure races and cross-host cancellation remain pending.
The earlier startup failure remains unresolved. Full M02 qualification remains pending on both hosts.
No normal session or database changed. M02 and Spatial-M1 remain incomplete.
'''.encode())
print(json.dumps(dict(source_files=341, selected_cases_per_profile=count, attachments=len(focused['attachments']))))
