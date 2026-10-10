"""Collect reviewed diagnostic outputs without scratch data or private fixture state."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
names = ('m02-durability-pressure-01', 'm02-durability-budget-02', 'm02-production-budget-03')
archive = base / 'm02-durability-diagnosis-01-sb16.tar.gz'
assert not archive.exists()
sha = lambda data: hashlib.sha256(data).hexdigest()
files = {}
reviews = []


def add(relative, path):
    assert relative not in files and not path.is_symlink()
    data = path.read_bytes()
    assert b'PRIVATE ' + b'KEY-----' not in data
    assert b'Bearer ' + b'fleet-' not in data
    files[relative] = dict(path=path, sha256=sha(data), bytes=len(data))


for index, name in enumerate(names):
    root = base / name
    review = json.loads((root / 'review.json').read_bytes())
    assert review['status'] == 'diagnostic_complete_review_pending'
    assert review['before'] == review['after']
    assert review['before']['source_files'] == 351 and review['before']['locked_files'] == 41
    assert not review['production_changed'] and not review['assertions_changed']
    assert not review['durability_changed'] and not review['monitor_errors']
    assert review['deadlines_changed'] == (index != 0)
    assert len(review['phases']) == 3
    phase_results = []
    for phase in review['phases']:
        expected = phase['phase'] != 'pressure' or index == 2
        assert phase['result']['passed'] == expected
        assert phase['result']['tests'] == 1 and phase['result']['skipped'] == 0
        assert phase['trace']['trace_complete']
        assert phase['exit_code'] == phase['trace']['command_exit_code'] == (0 if expected else 1)
        phase_results.append(dict(phase=phase['phase'], **phase['result']))
    for relative, digest in review['attachments'].items():
        path = Path(relative)
        assert not path.is_absolute() and '..' not in path.parts and path.name != 'scratch.bin'
        add(name + '/' + relative, root / path)
        assert files[name + '/' + relative]['sha256'] == digest
    add(name + '/review.json', root / 'review.json')
    reviews.append(dict(run=name, results=phase_results, source_commit=review['source_commit']))

health = base / 'm02-storage-health-01'
for name in ('review.json', 'error-log.json', 'package-policy.log', 'package-download.log'):
    add('m02-storage-health-01/' + name, health / name)

scan = []
for name, item in files.items():
    if not name.endswith('.log'):
        continue
    text = item['path'].read_text(errors='replace')
    markers = re.findall(r'ERROR: AddressSanitizer|ERROR: LeakSanitizer|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:|WARNING: ThreadSanitizer', text)
    assert not markers, (name, markers)
    scan.append(name)

record = dict(status='verified_diagnostics', runs=reviews, archive=str(archive),
              attachment_count=len(files), scanned_logs=len(scan), sanitizer_matches=0,
              historical_failures_resolved=False, phase_complete=False,
              scope='Three single-case diagnostic comparisons. Changed client budgets are not qualification passes.',
              collector_sha256=sha(Path(__file__).read_bytes()),
              attachments={name:{k:v for k,v in item.items() if k != 'path'} for name,item in files.items()})
manifest = base / 'm02-durability-diagnosis-01-manifest.json'
assert not manifest.exists()
manifest.write_text(json.dumps(record, indent=2) + '\n')
with tarfile.open(archive, 'x:gz') as output:
    for name, item in sorted(files.items()):
        output.add(item['path'], arcname=name, recursive=False)
    output.add(manifest, arcname='manifest.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive.read_bytes()),
                     attachment_count=len(files), scanned_logs=len(scan), status=record['status'])))
