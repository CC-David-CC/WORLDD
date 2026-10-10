"""Preserve both qualification profiles and the original runner failure."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
old = base / 'm02-revocation-command-01'
root = base / 'm02-revocation-command-02'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest = json.loads((root / 'manifest.json').read_bytes())
original = json.loads((old / 'manifest.json').read_bytes())
profiles = {}
attachments = {}

def add(path):
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base)
    assert path.suffix in ('.json', '.log', '.pb', '.txt', '.cpp', '.py', '.proto')
    data = path.read_bytes()
    assert b'PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
    attachments[path.relative_to(base).as_posix()] = sha(path)

for candidate, inputs in ((old, original), (root, manifest)):
    assert all(sha(candidate / 'source' / name) == digest for name, digest in inputs['files'].items())
    assert all(inputs['files'][name] == digest for name, digest in inputs['locked_files'].items())
    add(candidate / 'manifest.json')
    for name in inputs['changed']: add(candidate / 'source' / name)
    for path in (candidate / 'checks').rglob('*'):
        if path.is_file(): add(path)

failure = json.loads((old / 'checks/debug/review.json').read_bytes())
assert failure['status'] == 'failed'
assert failure['commands'][-1]['exit_code'] == 8
assert "No module named 'core_pb2'" in (old / 'checks/debug/focused.log').read_text()
debug_initial = json.loads((root / 'checks/debug/review.json').read_bytes())
assert debug_initial['status'] == 'failed'
assert debug_initial['commands'][-1]['label'] == 'issuer-client' and debug_initial['commands'][-1]['exit_code'] == 1
assert 'FileNotFoundError' in (root / 'checks/debug/issuer-client.log').read_text()
assert 'spatial-issuer-client-probe' in (root / 'checks/debug/issuer-client.log').read_text()
assert 'FAILED (errors=1)' in (root / 'checks/debug/issuer-client.log').read_text()
for profile in ('debug', 'runtime-sanitize'):
    out = root / 'checks' / profile
    final_out = root / 'checks/debug-retry' if profile == 'debug' else out
    record = json.loads((final_out / 'review.json').read_bytes())
    assert record['status'] == 'passed'
    build = old / profile / 'build'
    assert all(sha(build / name) == digest for name, digest in record['binaries'].items())
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in (build / 'CMakeCache.txt').read_text()
    assert record['compiled_inputs'] == original['files'] and record['checked_inputs'] == manifest['files']
    counts = {}
    selected = [(command, out) for command in debug_initial['commands']] if profile == 'debug' else []
    selected += [(command, final_out) for command in record['commands']]
    for command, location in selected:
        log = location / (command['label'] + '.log')
        assert sha(log) == command['sha256']
        initial_probe_failure = profile == 'debug' and location == out and command['label'] == 'issuer-client'
        assert command['exit_code'] == (1 if initial_probe_failure else 0)
        if 'count' not in command: continue
        text = log.read_text()
        assert 'skipped=' not in text and 'expected failures=' not in text
        passed = len(re.findall(r'^test_.* \.\.\. ok$', text, re.M))
        assert passed == command['count'] - (1 if initial_probe_failure else 0)
        assert initial_probe_failure or '\nOK\n' in text
        counts[command['label']] = counts.get(command['label'], 0) + passed
    assert len(counts) == 5 and counts['command'] == 12
    profiles[profile] = dict(suites=len(counts), counts=counts, cases=sum(counts.values()), binaries=record['binaries'])

for name in ('check-revocation-command-01.py', 'check-revocation-command-02.py', 'check-revocation-command-03.py', 'collect-revocation-command-02.py'):
    add(base / name)
review = dict(status='focused_passed_phase_gate_pending', host='sb16', source_files=len(manifest['files']),
    locked_files_unchanged=len(manifest['locked_files']), profiles=profiles, attachments=attachments,
    baseline='The existing raw issuer client returns commit-only identity_status; the new command joins it to checked cancellation barriers.',
    failure='Runner01 built successfully but all five test imports failed because imported Python protobuf targets were omitted. No cases executed.',
    correction='Runner02 generates all imported bindings. Its Debug issuer-client suite passed 27 cases and errored on one missing probe executable. Runner03 builds that probe and passes the one affected case, then runs all five suites under sanitizers. The command test imports its helper module without collecting the helper test class.',
    limitations=['No new native, physical-input, performance, or both-host gate qualification.',
        'Two previously preserved intermittent M02 failures remain unresolved.', 'M02 and Spatial-M1 remain incomplete.'])
review_path = root / 'primary-review.json'
with review_path.open('xb') as stream: stream.write((json.dumps(review, indent=2) + '\n').encode())
archive = base / 'revocation-command-evidence-02.tar'
with tarfile.open(archive, 'x') as tar:
    for name in sorted(attachments): tar.add(base / name, arcname='sb16/' + name, recursive=False)
    tar.add(review_path, arcname='primary-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), profiles=profiles, attachments=len(attachments))))
