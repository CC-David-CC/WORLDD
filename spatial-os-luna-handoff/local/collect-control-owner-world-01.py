"""Collect completed real-world owner checks without exporting fixture state."""
import hashlib
import io
import json
from pathlib import Path
import re
import socket
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
out = r / 'local/control-owner-world-01'
run = json.loads((out / 'summary.json').read_text())
assert run['host'] == host and run['status'] == 'passed' and not run['compiler_overlap']
assert not run['production_supervisor_binding'] and not run['production_recovery_release']
pid = {'sb16': 1693702, 'sb16-2': 1475338}[host]
marker = Path('/proc') / str(pid) / 'cmdline'
assert not marker.exists() or b'qualify-control-owner-world-01.py' not in marker.read_bytes()
previous = r / 'local/m02-control-owner-03'
manifest = json.loads((previous / 'manifest.json').read_text())

def sha(data):
    return hashlib.sha256(data).hexdigest()

assert run['source_manifest'] == manifest['files']
assert all(sha((previous / 'source' / n).read_bytes()) == h for n, h in manifest['files'].items())
assert sha((previous / 'qualification/summary.json').read_bytes()) == run['preceding_summary_sha256']
assert sha((r / 'local/test_control_owner_world_01.py').read_bytes()) == run['test_sha256']
monitor = [json.loads(line) for line in (out / 'compiler-monitor.jsonl').read_text().splitlines()]
assert monitor and not any(row['compilers'] for row in monitor)
attachments = {}

def collect(path):
    data = path.read_bytes()
    assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
    attachments[path.relative_to(out).as_posix()] = data
    return data

checks = {
    'test_native_revocation_releases_managed_reservation':
        ('reservation_released', 'authorized_handoff_delivered_native_input'),
    'test_world_crash_retains_only_its_owned_reservations':
        ('world_exit_confirmed', 'compositor_exit_confirmed', 'exact_owner_fenced',
         'crashed_reservation_retained', 'other_owner_untouched', 'other_owner_release_succeeded'),
    'test_explicit_channel_fence_releases_native_input_without_stopping_application':
        ('world_exit_confirmed', 'compositor_exit_confirmed', 'reservation_retained_pending_recovery')}
result = dict(host=host, candidate='owner03', profiles={}, production_supervisor_binding=False,
    production_recovery_release=False, integration_ready=False)
assert set(run['profiles']) == {'debug', 'runtime-sanitize'}
prior = json.loads((previous / 'qualification/summary.json').read_text())
for profile, value in run['profiles'].items():
    assert value['exit_code'] == 0 and value['binaries'] == prior['profiles'][profile]['binaries']
    build = Path(prior['profiles'][profile]['build'])
    assert all(sha((build / n).read_bytes()) == h for n, h in value['binaries'].items())
    assert monitor[0]['time'] <= value['started'] and monitor[-1]['time'] >= value['ended'] - 2
    log = collect(out / profile / 'test.log').decode()
    assert re.findall(r'^Ran (\d+) tests? in ', log, re.M) == ['3'] and log.rstrip().endswith('OK')
    assert set(re.findall(r'^(test_\w+) .* \.\.\. ok$', log, re.M)) == set(checks)
    assert '... skipped' not in log and 'skipped=' not in log
    assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL', log)
    witnesses = list((out / profile / 'witness').glob('*.json'))
    assert {p.stem for p in witnesses} == set(checks)
    for path in witnesses:
        witness = json.loads(collect(path))
        assert witness['native_release_confirmed'] and witness['application_alive']
        assert not witness['production_supervisor_binding'] and not witness['production_recovery_release']
        assert all(witness[field] for field in checks[path.stem])
    for path in (out / profile / 'native').rglob('*.log'):
        collect(path)
    result['profiles'][profile] = dict(native_world_checks=3, failed=0, skipped=0, binaries=value['binaries'])
collect(out / 'summary.json')
collect(out / 'compiler-monitor.jsonl')
result['attachment_hashes'] = {n: sha(data) for n, data in attachments.items()}
attachments['collected.json'] = (json.dumps(result, indent=2) + '\n').encode()
archive = r / 'local' / ('control-owner-world-01-' + host + '.tar.gz')
assert not archive.exists()
with tarfile.open(archive, 'w:gz') as target:
    for name, data in attachments.items():
        entry = tarfile.TarInfo('owner-world-01/' + host + '/' + name)
        entry.size = len(data)
        entry.mode = 0o600
        target.addfile(entry, io.BytesIO(data))
print(json.dumps(dict(host=host, profiles=2, checks_per_profile=3, archive=str(archive))))
