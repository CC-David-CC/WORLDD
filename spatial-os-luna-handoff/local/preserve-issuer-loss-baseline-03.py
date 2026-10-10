"""Verify and store original issuer-loss failures in the project evidence tree."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
root = Path(__file__).resolve().parents[1]
archive = root / 'local/issuer-loss-baseline-03-evidence.tar'
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
assert digest == 'c93cc82d961de439a5ba5203001cd01ac997aa61b83aaa134eb9bc2f92ab0c42'
target = root / 'evidence/control-recovery-2026-10-09/issuer-loss-baseline-03'
target.mkdir(exist_ok=False)
with tarfile.open(archive) as tar:
    members = tar.getmembers(); assert len(members) == len({m.name for m in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert target.joinpath('sb16', *name.parts).resolve().is_relative_to(target.resolve())
    review = json.load(tar.extractfile('issuer-loss-baseline-03-review.json'))
    assert {m.name for m in members} == set(review['attachments']) | {'issuer-loss-baseline-03-review.json'}
    for member in members:
        data = tar.extractfile(member).read()
        if member.name in review['attachments']:
            assert hashlib.sha256(data).hexdigest() == review['attachments'][member.name]
        path = target / 'sb16' / member.name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
report = dict(status='confirmed_missing_deferred_fence', source_commit='3692e8a4fcd33c0622832b88022c60d6e89003dd',
    archive_sha256=digest, attachments_verified=len(review['attachments']),
    reviewer='Primary agent', preserved_failed_assumptions=['baseline-01', 'baseline-02'],
    confirmed_failure='baseline-03', final_pending_obligations=2,
    conclusions=['Actual process and native cleanup succeeds with issuer unavailable.',
        'Issuer restart preserves two pending revocation obligations.',
        'Retained dependency recovery succeeds; both retirements reject missing issuer fences.'],
    collector_correction='Corrected AuthorityBarrierState to the actual AuthorityBarrierStatus schema before collection. Raw runtime evidence unchanged.',
    full_M02_gate='pending on both hosts')
(target / 'primary-review.json').write_text(json.dumps(report, indent=2) + '\n')
state_path = root / 'local/driver-state.json'; state = json.loads(state_path.read_bytes())
state['previous_goal_turn'] = dict(classification='no progress', detail='Answered test-schedule question; no authoritative task state changed in that response.')
state['last_turn'] = dict(classification='progress', detail='Verified terminal baseline, reproduced missing deferred fencing, and implemented a focused candidate.')
state['commit'] = '3692e8a4fcd33c0622832b88022c60d6e89003dd'
state['issuer_loss_recovery'] = dict(status='candidate_tests_running', baseline=str(target.relative_to(root)).replace('\\', '/') + '/primary-review.json',
    candidate='m02-deferred-fence-01', source_files=348, locked_files=41, host='sb16', debug_session=89302,
    sanitizer_status='unrun', full_phase_gate='pending on both hosts')
state_path.write_text(json.dumps(state, indent=2) + '\n')
print('Preserved and verified 52 baseline attachments; implementation remains under focused qualification')
