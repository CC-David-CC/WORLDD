"""Integrate exact selected evidence; keep M02 and Spatial-M1 incomplete."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
root = Path(__file__).resolve().parents[1]
archive = root / 'local/deferred-fence-01-evidence.tar'
digest = hashlib.sha256(archive.read_bytes()).hexdigest(); assert len(sys.argv) == 2 and digest == sys.argv[1]
target = root / 'evidence/control-recovery-2026-10-09/deferred-fence-01'; target.mkdir(exist_ok=False)
with tarfile.open(archive) as tar:
    members = tar.getmembers(); assert len(members) == len({member.name for member in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert target.joinpath('sb16', *name.parts).resolve().is_relative_to(target.resolve())
    collected = json.load(tar.extractfile('collected-review.json'))
    assert collected['status'] == 'focused_passed' and collected['host'] == 'sb16'
    assert {m.name for m in members} == set(collected['attachments']) | {'collected-review.json'}
    assert all(value['passed'] == 49 and value['failed'] == 0 and value['skipped'] == 0 for value in collected['profiles'].values())
    for name, expected in collected['source'].items():
        assert hashlib.sha256((root / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected, name
    for member in members:
        data = tar.extractfile(member).read()
        if member.name in collected['attachments']: assert hashlib.sha256(data).hexdigest() == collected['attachments'][member.name]
        path = target / 'sb16' / member.name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
review = dict(status='focused_increment_reviewed', reviewer='Primary agent',
    source_files=348, locked_files_unchanged=41, integrated_source=collected['source'],
    archive_sha256=digest, attachments_verified=len(collected['attachments']), profiles=collected['profiles'],
    baseline='evidence/control-recovery-2026-10-09/issuer-loss-baseline-03/primary-review.json',
    review_findings=[
        'Deferred fencing requires retained exact cleanup and the checked failed supervisor exit.',
        'The private coordinator path uses original authorization and fresh issuer-bound proofs.',
        'Launch and dependent receipt bytes remain immutable; new fence storage is separate.',
        'Schemas 5 through 8 validate stopped roles, exact generation, and retirement digest.',
        'Ordinary unfenced shutdown rejection remains covered by the unchanged regression suites.',
        'Local commit failures preserve remote fences and retirements for exact explicit retries.',
        'No protobuf field, locked acceptance test, original deadline, or normal session changed.'],
    preserved_failures=['Baseline 01 and 02 incorrectly expected automatic management-daemon exit.',
        'Baseline 03 reproduced the production missing-fence defect.',
        'The first Debug launch command supplied an extra argument; all 14 cases errored before launch. Only that suite was rerun.'],
    limitations=['Focused checks ran on sb16. Full M02 qualification remains pending on both hosts.',
        'Two earlier intermittent failures remain unresolved.',
        'These software-display checks do not qualify GPU scanout, physical input, or performance.'])
(target / 'primary-review.json').write_text(json.dumps(review, indent=2) + '\n', newline='\n')
section = '''## 2026-10-10: Issuer-outage recovery passes focused native checks

The retained coordinator can now finish fencing after issuer loss during failed-supervisor cleanup.
It requires the original checked failure receipt, matching supervisor exit, and original signing configuration.
Original process, native, and dependent cleanup bytes remain unchanged.
A separate deferred fence precedes authenticated retirement. Unapplied revocations retain applied revision zero.
Ordinary unfenced shutdowns remain ineligible. A reopened journal cannot recreate session ownership.

All 49 selected checks passed in Debug and runtime ASan/UBSan on sb16: 98 checks total.
Five new scenarios cover issuer restart, local and remote persistence failure, altered receipts, wrong keys, and exact retries.
The 44 related checks cover coordinator retirement, broker retirement, stopped-broker dependencies, broker cleanup, and launch ownership.
Read evidence/control-recovery-2026-10-09/deferred-fence-01/primary-review.json.
All 348 source hashes match. All 41 locked files remain unchanged.

The original defect remains preserved in issuer-loss-baseline-03.
Earlier baseline assumptions and the first Debug launch-runner argument error also remain preserved.
The corrected launch command reran only its affected 14 cases. No runtime or test assertion changed for that correction.

Next: audit remaining M02 requirements and resolve the two preserved intermittent failures before the complete phase gate.
Full M02 qualification remains pending on both hosts. M02 and Spatial-M1 remain incomplete.
No normal desktop session or database changed. GPU, physical-input, and performance qualification remain pending.

'''
path = root / 'CURRENT_STATE.md'; data = path.read_text(); assert data.startswith('# Implementation status\n')
path.write_text(data.replace('# Implementation status\n\n', '# Implementation status\n\n' + section, 1), newline='\n')
path = root / 'docs/DEFERRED_RECOVERY_FENCE.txt'; data = path.read_text()
path.write_text(data.replace('Status: Proposed production correction. Qualification remains pending.',
    'Status: Focused Debug and runtime ASan/UBSan checks passed on sb16. Full M02 qualification remains pending.'), newline='\n')
path = root / 'tasks/m1/identity.control_recovery.json'; task = json.loads(path.read_bytes())
task['current_step'] = 'm02_failure_evidence_and_gate_audit'
task['remaining'] = ['Audit remaining M02 recovery and cancellation requirements against current evidence before freezing the gate.',
    'Resolve the preserved participant-approval startup failure and active-generation revocation-read timeout.',
    'Run complete required M02 Debug, runtime sanitizer, harness, acceptance, native, and cross-host qualification on both hosts.']
task['deferred_fencing'] = dict(state='Focused issuer-outage recovery passed with immutable cleanup and checked deferred fencing.',
    evidence=str(target.relative_to(root)).replace('\\', '/') + '/primary-review.json',
    contract='docs/DEFERRED_RECOVERY_FENCE.txt', host='sb16', cases_per_profile=49, new_cases=5,
    profiles=['Debug', 'runtime ASan/UBSan'], failed=0, skipped=0, full_phase_gate='pending on both hosts')
path.write_text(json.dumps(task, indent=2) + '\n', newline='\n')
path = root / 'tasks/m1/identity.supervisor_recovery.json'; task = json.loads(path.read_bytes())
task['historical_remaining_before_control_recovery'] = task['remaining']
task['current_step'] = 'm02_failure_evidence_and_gate_audit'
task['remaining'] = ['Follow tasks/m1/identity.control_recovery.json for the current integrated recovery evidence.',
    'Complete the remaining requirement audit, resolve preserved intermittent failures, and run the full M02 gate on both hosts.']
path.write_text(json.dumps(task, indent=2) + '\n', newline='\n')
debug = root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt'
with debug.open('a', newline='\n') as stream:
    stream.write('''
2026-10-10: Deferred issuer fencing fixes a reproduced outage-recovery gap.
All 49 selected cases passed in Debug and runtime sanitizers on sb16. Full M02 remains pending on both hosts.
Actual failed-supervisor process/native cleanup remains separate from issuer fencing and retirement.
The retained coordinator obtains the missing fence after issuer restart and preserves original cleanup bytes.
Retirement keeps applied revision zero. Wrong keys, altered cleanup, and altered fences remain rejected.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/deferred-fence-01/primary-review.json.
Baseline evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/issuer-loss-baseline-03/primary-review.json.
Baselines 01 and 02 incorrectly expected management daemons to exit automatically after identity-service loss.
Baseline 03 added actual pinned child crashes and reproduced both rejected retirements.
The baseline collector initially used the wrong protobuf type name. Correcting that name did not change raw evidence.
The first Debug launch command passed an extra source argument. Its 14 cases errored before launching production.
The corrected command reran only those 14 cases. All passed. The five other suites were not repeated.
All 348 frozen source hashes match. All 41 locked files remain unchanged.
No normal session or database changed. These checks do not show physical input, GPU scanout, or native performance.
Next: audit remaining M02 requirements and resolve the two preserved intermittent failures before the phase gate.
M02 and Spatial-M1 remain incomplete.
''')
path = root / 'local/driver-state.json'; state = json.loads(path.read_bytes())
state['current_step'] = 'm02_failure_evidence_and_gate_audit'
state['last_turn'] = dict(classification='progress', detail='Fixed reproduced issuer-outage retirement failure; 49 focused cases passed in each profile on sb16.')
state['issuer_loss_recovery'].update(status='reviewed_focused_passed', debug_session=None, sanitizer_session=None,
    sanitizer_status='passed', cases_per_profile=49, new_cases=5)
state['issuer_loss_recovery']['evidence'] = str(target.relative_to(root)).replace('\\', '/') + '/primary-review.json'
path.write_text(json.dumps(state, indent=2) + '\n', newline='\n')
print(json.dumps(dict(status=review['status'], attachments=review['attachments_verified'], profiles=review['profiles'])))
