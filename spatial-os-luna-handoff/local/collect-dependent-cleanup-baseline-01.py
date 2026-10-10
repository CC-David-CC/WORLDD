"""Preserve the fixture naming error and the corrected durability baseline."""
import hashlib
import json
from pathlib import Path
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
files = {}; records = []
for number in ('01', '02'):
    root = r / ('local/m02-dependent-cleanup-baseline-' + number)
    run = json.loads((root / 'review.json').read_bytes()); records.append(run)
    assert run['exit_code'] == 1 and sha(root / 'baseline.log') == run['log_sha256']
    log = (root / 'baseline.log').read_text()
    if number == '01':
        assert run['status'] == 'unexpected_result' and "TypeError: 'PosixPath' object is not callable" in log
        assert 'FAILED (errors=1)' in log
    else:
        assert run['status'] == 'missing_durable_dependent_receipt_reproduced'
        assert '1 != 2 : Checked broker release has no durable dependent receipt migration' in log
        assert 'FAILED (failures=1)' in log
    test = r / ('local/test-dependent-cleanup-baseline-' + number + '.py')
    assert sha(test) == run['test_sha256']
    files[number + '/input.py'] = test
    for path in root.rglob('*'):
        if path.is_file() and (path.suffix == '.log' or path.name == 'review.json'):
            files[number + '/' + path.relative_to(root).as_posix()] = path
assert records[0]['source'] == records[1]['source'] and records[0]['binaries'] == records[1]['binaries']
candidate = r / 'local/m02-selective-recovery-01'
assert all(sha(candidate / 'source' / n) == h for n, h in records[1]['source'].items())
assert all(sha(candidate / 'debug/build' / n) == h for n, h in records[1]['binaries'].items())
assert sha(Path(records[1]['server']['path'])) == records[1]['server']['sha256']
summary = dict(status='durability_gap_reproduced_on_unchanged_binaries', host='sb16',
    fixture_error='The first test helper named journal collided with an inherited Path attribute.',
    correction='Rename the helper to world_database. Keep the original failed run and input.',
    finding='Actual broker recovery succeeds, but launch.db stays at version 1 without a dependent cleanup receipt.',
    cases_per_run=1, first_run_errors=1, corrected_run_expected_failures=1,
    source_files=len(records[1]['source']), attachments={n: sha(p) for n, p in files.items()})
target = r / 'local/dependent-cleanup-baseline-review-01.json'
with target.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/dependent-cleanup-baseline-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name, path in files.items(): tar.add(path, arcname='dependent-cleanup-baseline01/sb16/' + name, recursive=False)
    tar.add(target, arcname='dependent-cleanup-baseline01/sb16/baseline-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), attachments=len(files))))
