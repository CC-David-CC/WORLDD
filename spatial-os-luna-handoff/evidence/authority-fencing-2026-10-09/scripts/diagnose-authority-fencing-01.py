"""Preserve the missing fencing baseline against exact qualified broker binaries."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
source = r / 'local/m02-supervisor-broker-handoff-02/source'
manifest = json.loads((r / 'local/supervisor-broker-handoff-candidate-02.json').read_text())['files']
assert len(manifest) == 283
for files in (manifest, json.loads((source / 'acceptance.lock.json').read_text())['files']):
    for name, digest in files.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
qualified = json.loads((r / ('local/supervisor-broker-full-summary-final-' + host + '-02.json')).read_text())
out = r / 'local/authority-fencing-baseline-01'; out.mkdir()
bindings = out / 'python'; bindings.mkdir()
proto = r / 'local/authority_fence_baseline.proto'
test = r / 'local/test_authority_fencing_baseline.py'
subprocess.run(['protoc', '--proto_path=' + str(proto.parent), '--python_out=' + str(bindings), str(proto)], check=True)
summary = dict(host=host, source_files=283, production_changed=False, acceptance_pass=False,
    test_sha256=hashlib.sha256(test.read_bytes()).hexdigest(), schema_sha256=hashlib.sha256(proto.read_bytes()).hexdigest(), profiles={})
for profile in ('debug', 'runtime-sanitize'):
    result = qualified[profile]
    assert tuple(result[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped')) == (1393,93,51,151,0,0)
    target = out / profile; target.mkdir()
    build = source.parent / profile / 'build'
    for name, digest in result['binaries'].items():
        assert hashlib.sha256((build / name).read_bytes()).hexdigest() == digest, name
    command = ['python3', str(test), str(source), str(build / 'identityd'), str(build / 'spatial-issuer-client')]
    env = dict(os.environ, PYTHONPATH=str(bindings) + ':' + str(build / 'python') + ':' + str(source),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    with (target / 'test.log').open('xb') as log:
        run = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=60)
    text = (target / 'test.log').read_text()
    expected = run.returncode == 1 and 'Ran 1 test' in text and 'FAILED (failures=1)' in text and \
        'Missing authority fencing challenge from the production issuer:' in text and 'code: INVALID_ARGUMENT' in text
    marker = any(value in text for value in ('AddressSanitizer','LeakSanitizer','runtime error:'))
    record = dict(command=command, exit_code=run.returncode, expected_missing_fencing=expected,
        sanitizer_marker=marker, acceptance_pass=False)
    (target / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    summary['profiles'][profile] = record
    print(host, profile, 'missing fencing confirmed', expected, flush=True)
(out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
with tarfile.open(r / ('local/authority-fencing-baseline-' + host + '-01.tar.gz'), 'w:gz') as archive:
    for path in sorted(out.rglob('*')):
        if path.is_file() and path.suffix in ('.log', '.json'):
            archive.add(path, arcname='baseline-01/' + host + '/' + path.relative_to(out).as_posix())
raise SystemExit(0 if all(v['expected_missing_fencing'] and not v['sanitizer_marker'] for v in summary['profiles'].values()) else 1)
