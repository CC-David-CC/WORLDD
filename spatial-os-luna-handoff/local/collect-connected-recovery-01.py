"""Collect terminal focused evidence, including the baseline and fixture failure."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] == 'sb16'
root = r / 'local/m02-connected-recovery-01'
original = json.loads((root / 'checks/review.json').read_text())
retry = json.loads((root / 'checks-retry02/review.json').read_text())
followup = json.loads((root / 'checks-followup03/review.json').read_text())
assert original['status'] == 'failed' and retry['status'] == 'failed'
assert all(item['exit_code'] == 0 for item in followup.values())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(root / 'checks/review.json') == retry['original_review_sha256']
assert sha(r / 'local/test_supervisor_child_handle_02.py') == retry['helper_sha256']
files = {}
for folder, record in (('checks', original), ('checks-retry02', retry)):
    for profile, results in record['profiles'].items():
        for name, item in results.items():
            if isinstance(item, dict) and 'log_sha256' in item:
                path = root / folder / profile / (name + '.log')
                assert sha(path) == item['log_sha256']
    for path in (root / folder).rglob('*'):
        if path.is_file() and path.suffix == '.log':
            files[str(path.relative_to(root))] = sha(path)
    path = root / folder / 'review.json'
    files[str(path.relative_to(root))] = sha(path)
for name, item in followup.items():
    assert sha(root / 'checks-followup03' / (name + '.log')) == item['log_sha256']
for path in (root / 'checks-followup03').rglob('*'):
    if path.is_file() and path.suffix in ('.log', '.json'):
        files[str(path.relative_to(root))] = sha(path)
debug = (root / 'checks/debug/focused.log').read_text()
san = (root / 'checks-retry02/runtime-sanitize/related.log').read_text()
counts = [(int(suite), int(count)) for suite, count in re.findall(r'^(\d+): Ran (\d+) tests? in ', debug, re.M)]
assert len(counts) == 7 and counts[-1] == (106, 8)
debug_related = sum(n for suite, n in counts if suite != 106)
san_counts = re.findall(r'^\d+: Ran (\d+) tests? in ', san, re.M)
assert len(san_counts) == 6 and sum(map(int, san_counts)) == debug_related
assert '83% tests passed, 1 tests failed out of 6' in san
assert 'Participant failed during supervisor approval' in san and 'FAILED (failures=1)' in san
assert 'FAILED (failures=1)' in debug
for name in ('checks-retry02/debug/handle.log', 'checks-followup03/handle.log'):
    text = (root / name).read_text()
    assert 'Ran 8 tests in ' in text and '\nOK\n' in text and text.count(' ... ok') == 8
    assert not any(marker in text for marker in ('ERROR:', 'FAIL:', 'AddressSanitizer:', 'LeakSanitizer:', 'runtime error:'))
for n, h in retry['source'].items():
    assert sha(root / 'source' / n) == h
assert sha(Path(retry['server']['path'])) == retry['server']['sha256']
for profile, results in retry['profiles'].items():
    for n, h in results['binaries'].items():
        assert sha(root / profile / 'build' / n) == h
review = dict(status='transfer_passed_related_startup_failure_unresolved', host='sb16', new_transfer_cases_per_profile=8,
    related_debug_passed=debug_related, related_sanitizer_passed=debug_related - 1,
    related_sanitizer_failed=1, unchanged_failing_case_repetitions_passed=5,
    profiles=['debug', 'runtime-sanitize'], source_files=len(retry['source']),
    locked_files=len(retry['locked']), attachments=files,
    baseline='Old supervisor rejected the required new channel; one expected failure preserved.',
    fixture_failure='One alias test hit an existing descriptor-target collision first. Seven new cases and six related suites passed.',
    correction='Only the alias-negative fixture now uses native target 39. Its aliased endpoint maps to free target 38.',
    corrected_helper_sha256=retry['helper_sha256'], production_source_changed_after_failure=False,
    unresolved='One existing owner-binding startup failed during participant approval. The same case passed five fresh sanitizer repetitions. Root cause is unproven.',
    missing=['Production coordinator receiving interface', 'Selective recovery release', 'Durable receipt and issuer retirement',
        'Complete M02 and both-host qualification'])
summary = root / 'focused-review.json'
with summary.open('x') as stream:
    json.dump(review, stream, indent=2); stream.write('\n')
archive = r / 'local/connected-recovery-focused-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in files:
        tar.add(root / name, arcname='connected-01/sb16/' + name, recursive=False)
    tar.add(summary, arcname='connected-01/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), archive_sha256=sha(archive), cases_per_profile=debug_related + 8,
    attachments=len(files), status=review['status'])))
