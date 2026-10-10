"""Preserve the failed candidate without replacing any original evidence."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda path, data: path.write_bytes((json.dumps(data, indent=2) + '\n').encode())
digest = '27a6a46c04187d3e4980612f38f95d96ab99720e488a8a67d3fd7805e230eb67'
archive = root / 'local/approval-interruption-checks-02-sb16.tar.gz'
assert sha(archive.read_bytes()) == digest
evidence = root / 'evidence/control-recovery-2026-10-09'
prefix = 'approval-interruption-failure02/sb16/'
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
        with path.open('xb') as stream:
            stream.write(tar.extractfile(member).read())
collected = json.loads((target / 'collected-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert run['status'] == 'failed' and collected['status'] == 'preserved_failure'
assert all(sha((target / name).read_bytes()) == value for name, value in collected['attachments'].items())
assert len(run['source']) == 345 and len(run['locked']) == 41
assert all(sha((root / name).read_bytes().replace(b'\r\n', b'\n')) == value for name, value in run['source'].items())
case = 'test_revocation_requires_child_acknowledgement_with_automatic_delivery'
profiles = {}
for profile in ('debug', 'runtime-sanitize'):
    logs = [(target / 'checks' / profile / (name + '.log')).read_text() for name in ('replacement', 'focused')]
    counts = [int(count) for log in logs for count in re.findall(r'^\d+: Ran (\d+) tests? in ', log, re.M)]
    assert counts == [3, 14, 6, 11, 18], counts
    assert not any(marker in log for log in logs for marker in ('... skipped', 'skipped=', 'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'runtime error:'))
    failed = 0 if profile == 'debug' else 1
    assert sum(log.count('FAILED (failures=1)') for log in logs) == failed
    profiles[profile] = dict(passed=52-failed, failed=failed, skipped=0, selected=52)
stderr = list((target / 'checks/runtime-sanitize/native').glob(case + '-supervisor-*.log'))
assert len(stderr) == 1
assert 'stage=revocations cause=Issuer client deadline exceeded' in stderr[0].read_text()
review = dict(status='preserved_failure_not_integrated', host='sb16', archive_sha256=digest,
    attachments_verified=len(collected['attachments']), profiles=profiles,
    failed_case='AuthoritySupervisorTests.' + case,
    observed_cause='The participant exceeded its issuer revocation-read deadline.',
    underlying_cause='Unconfirmed. The saved stderr does not identify why the deadline expired.',
    historical_startup_cause='Unconfirmed. This failure occurred after activation, not during approval.',
    production_source_changed=False, locked_files_unchanged=41,
    next_check='Run only this unchanged case up to five times with pinned sanitizer binaries and separate evidence paths.',
    full_phase_gate='Pending on both hosts. M02 and Spatial-M1 remain incomplete.')
write(target / 'failure-review.json', review)
driver_path = root / 'local/driver-state.json'
driver = json.loads(driver_path.read_bytes())
driver['approval_interruption_check'].update(status='failed_related_sanitizer_case', session=None,
    terminal_session=78416, exit_code=1, profiles=profiles,
    failure_evidence=str((target / 'failure-review.json').relative_to(root)).replace('\\', '/'),
    failure_archive_sha256=digest, underlying_cause='unconfirmed')
write(driver_path, driver)
note = '''
2026-10-10: Approval interruption candidate02 has one related sanitizer failure.
Debug: 52 passed. Runtime sanitizers: 51 passed, 1 failed. No cases were skipped.
The three new interruption scenarios passed in both profiles.
The failing case is AuthoritySupervisorTests.test_revocation_requires_child_acknowledgement_with_automatic_delivery.
The saved participant diagnostic reports: stage=revocations cause=Issuer client deadline exceeded.
The underlying timeout cause is unconfirmed. This is not proof of the historical approval-startup cause.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/approval-interruption-failure02/sb16/failure-review.json.
All source, binary, and raw log hashes were checked before collection. The original failed run remains intact.
No production code, timeout, or assertion changed. Candidate02 is not integrated or committed.
Next: only the unchanged failing case, up to five repetitions with separate sanitizer evidence.
Full M02 qualification remains pending on both hosts. M02 and Spatial-M1 remain incomplete.
'''
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(note.encode())
status_path = root / 'CURRENT_STATE.md'
status = status_path.read_text(encoding='utf-8')
heading = '# Implementation status\n'
assert status.startswith(heading)
update = '''
## 2026-10-10: Approval interruption candidate has a related sanitizer failure

The three new approval-interruption scenarios passed in both profiles on sb16.
All 52 selected Debug cases passed. Runtime sanitizers passed 51 cases and failed one.
The existing supervisor revocation case exceeded its issuer revocation-read deadline.
The underlying timeout cause remains unconfirmed. This happened after activation, not during approval.
Read evidence/control-recovery-2026-10-09/approval-interruption-failure02/sb16/failure-review.json.
The exact failure and stderr are preserved. The candidate remains unintegrated.
Next: narrow reproduction of the unchanged failing case with the same sanitizer binaries.
Full M02 qualification remains pending on both hosts. M02 and Spatial-M1 remain incomplete.

'''
status_path.write_bytes((heading + update + status[len(heading):]).encode())
print(json.dumps(review, indent=2))
