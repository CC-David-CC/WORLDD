"""Demonstrate missing failure diagnostics using unchanged production binaries."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-participant-diagnostics-01'; source = root / 'source'
prior_root = base / 'm02-broker-replacement-02'
prior = json.loads((prior_root / 'checks/review.json').read_bytes())
manifest = json.loads((root / 'manifest.json').read_bytes())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert prior['status'] == 'passed'
assert set(manifest['files']) == set(prior['source'])
assert {name for name in manifest['files'] if manifest['files'][name] != prior['source'][name]} == {
    'tests/core/test_identity_participant_runtime.py', 'tests/native/test_authority_supervisor.py'}
for path in Path('/proc').iterdir():
    if not path.name.isdigit(): continue
    try: name = (path / 'comm').read_text().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError): continue
    assert name not in ('ctest', 'ninja', 'cc1', 'cc1plus', 'clang', 'clang++'), (path, name)
build = prior_root / 'debug/build'
assert all(sha(source / name) == digest for name, digest in manifest['files'].items())
assert all(sha(prior_root / 'source' / name) == digest for name, digest in prior['source'].items())
assert all(sha(build / name) == digest for name, digest in prior['profiles']['debug']['binaries'].items())
target = root / 'checks'; target.mkdir(exist_ok=False)
command = ['python3', str(source / 'tests/core/test_identity_participant_runtime.py'), str(build / 'identityd'),
    str(build / 'spatial-issuer-client'), str(build / 'spatial-participant-runtime-probe'),
    'ParticipantRuntimeTests.test_stalled_transport_records_operation_and_cause_without_reconnect',
    'ParticipantRuntimeTests.test_peer_failure_diagnostic_is_bounded_and_cannot_add_log_lines', '-v']
record = dict(status='running', command=command, source=manifest['files'], locked=manifest['locked_files'],
    reused_builds=str(prior_root), build_provenance_sha256=sha(prior_root / 'checks/review.json'), started=time.time())
path = target / 'review.json'; path.write_text(json.dumps(record, indent=2) + '\n')
env = dict(os.environ, PYTHONPATH=str(build / 'python') + ':' + str(source), SPATIAL_SECURITY_EVIDENCE=str(target / 'native'))
with (target / 'baseline.log').open('xb') as stream:
    result = subprocess.run(command, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=60)
text = (target / 'baseline.log').read_text()
record.update(exit_code=result.returncode, ended=time.time(), log_sha256=sha(target / 'baseline.log'),
    status='missing_diagnostics_reproduced' if result.returncode == 1 and 'FAILED (failures=2)' in text else 'unexpected_result')
path.write_text(json.dumps(record, indent=2) + '\n')
print(text, flush=True)
assert record['status'] == 'missing_diagnostics_reproduced'
assert 'stage=issuer_connect' in text and 'stage=authentication_begin' in text
assert all(sha(source / name) == digest for name, digest in manifest['files'].items())
print(record['status'], flush=True)
