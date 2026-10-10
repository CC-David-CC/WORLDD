"""Preserve the failed fixture and checked follow-up without changing frozen source."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] == 'sb16'
root = r / 'local/m02-connected-recovery-02'
original = json.loads((root / 'checks/review.json').read_text())
retry = json.loads((root / 'checks-retry04/review.json').read_text())
assert original['status'] == 'failed' and retry['status'] == 'passed'
assert original['source'] == retry['source'] and original['locked'] == retry['locked']
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(r / 'local/test_session_broker_04.py') == retry['fixture_sha256']
files = {}
for folder, record in (('checks', original), ('checks-retry04', retry)):
    for profile, results in record['profiles'].items():
        for name, item in results.items():
            if isinstance(item, dict) and 'log_sha256' in item:
                assert sha(root / folder / profile / (name + '.log')) == item['log_sha256']
    for path in (root / folder).rglob('*'):
        if path.is_file() and (path.suffix == '.log' or path.name == 'review.json'):
            data = path.read_bytes()
            assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data, path
            files[path.relative_to(root).as_posix()] = sha(path)

def clean(text):
    assert not any(marker in text for marker in ('... skipped', 'skipped=', 'ERROR: AddressSanitizer',
        'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL', 'runtime error:'))

debug = (root / 'checks/debug/focused.log').read_text()
clean(debug)
counts = [int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ', debug, re.M)]
assert counts == [14, 13, 11, 8, 4, 15], counts
assert 'FAILED (errors=1)' in debug and 'unable to open database file' in debug
assert '83% tests passed, 1 tests failed out of 6' in debug
for profile in ('debug', 'runtime-sanitize'):
    corrected = (root / 'checks-retry04' / profile / 'session-broker-corrected.log').read_text()
    clean(corrected)
    assert re.findall(r'^Ran (\d+) tests? in ', corrected, re.M) == ['4']
    assert corrected.count(' ... ok') == 4 and '\nOK\n' in corrected
    assert retry['profiles'][profile]['session-broker-corrected']['exit_code'] == 0
    for name, digest in retry['profiles'][profile]['binaries'].items():
        assert sha(root / profile / 'build' / name) == digest
    cache = (root / profile / 'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
assert original['profiles']['debug']['binaries'] == retry['profiles']['debug']['binaries']
san = (root / 'checks-retry04/runtime-sanitize/focused.log').read_text()
clean(san)
assert [int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ', san, re.M)] == [14, 13, 11, 8, 15]
assert '100% tests passed, 0 tests failed out of 5' in san
for group in ('source', 'locked'):
    assert all(sha(root / 'source' / name) == digest for name, digest in retry[group].items())
assert sha(Path(retry['server']['path'])) == retry['server']['sha256']
review = dict(status='focused_passed_phase_gate_pending', host='sb16', new_receiver_cases=15,
    new_session_broker_cases=4, related_cases=46, unique_selected_cases_per_profile=65,
    profiles=['debug', 'runtime-sanitize'], source_files=len(retry['source']), locked_files=len(retry['locked']),
    original_result=dict(passed=64, errors=1, sanitizer='unrun after Debug fixture error'),
    corrected_result=dict(debug_passed=65, sanitizer_passed=65, failed=0, skipped=0),
    original_failure='Wrong-native-role fixture expected a cleanup journal although the supervisor rejected the sealed role before creating ownership.',
    correction='Assert the exact wrong-role diagnostic, absent launch journal, absent child participant configuration, and no activation.',
    production_changed_after_failure=False, original_debug_binaries_unchanged=True,
    corrected_fixture_sha256=retry['fixture_sha256'], attachments=files,
    prior_unresolved='connected01 owner-binding participant-approval startup failed once; five unchanged repetitions passed; cause unknown.',
    missing=['World supervisor ownership and registration', 'Selective checked recovery release',
        'Durable dependent receipt and issuer retirement', 'Complete M02 qualification on both hosts'])
summary = root / 'focused-review.json'
with summary.open('x') as out:
    json.dump(review, out, indent=2); out.write('\n')
archive = r / 'local/connected-recovery-focused-02-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in files:
        tar.add(root / name, arcname='connected-02/sb16/' + name, recursive=False)
    tar.add(summary, arcname='connected-02/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), archive_sha256=sha(archive), attachments=len(files),
    checks_per_profile=65, status=review['status'])))
