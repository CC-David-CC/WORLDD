"""Run one cross-host node only after the frozen local M04 gate passes review."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=('controller', 'worker'))
parser.add_argument('profile', choices=('Debug', 'ASan-UBSan'))
parser.add_argument('scenario', choices=('delay', 'crash'))
parser.add_argument('direction', choices=('forward', 'reverse'))
parser.add_argument('--check-only', action='store_true')
args = parser.parse_args()
root = Path('/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
gate_path = root / 'local/m04-full-gate-01/run.json'
gate = json.loads(gate_path.read_text())
primary = json.loads((gate_path.parent / 'primary-review.json').read_text())
assert gate['status'] == 'commands_passed_needs_review'
assert primary['status'] == 'reviewed_local_M04_gate_pending_crosshost'
assert primary['candidate'] == gate['candidate'] == 'f74c6b579c6149f41e1cbd17b025eb92fd453009'
assert primary['gate_sha256'] == hashlib.sha256(gate_path.read_bytes()).hexdigest()

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

for name, expected in gate['source_sha256'].items():
    assert digest(root / name) == expected, name
for name, expected in gate['installed_helpers'].items():
    assert digest(Path(name)) == expected, name
profile = gate['profiles'][args.profile]
assert profile['status'] == 'commands_passed_needs_review'
build = root / 'local/m04-gate-m04-full-gate-01' / args.profile
for name, expected in profile['binary_sha256'].items():
    assert digest(build / name) == expected, name
assert digest(Path(profile['server']['path'])) == profile['server']['sha256']
assert digest(Path(profile['launcher'])) == profile['launcher_sha256']
cross = root / 'local/m04-crosshost-01'
bindings = cross / 'python'
bindings.mkdir(parents=True, exist_ok=True)
schema = root / 'tests/proto/crosshost_revocation.proto'
includes = sorted({path.parent for path in (root / 'proto').rglob('*.proto')})
subprocess.run(['protoc', '--proto_path=' + str(schema.parent),
                *['--proto_path=' + str(path) for path in includes],
                '--python_out=' + str(bindings), str(schema)], check=True)
binding_hash = digest(bindings / 'crosshost_revocation_pb2.py')
source_digest = hashlib.sha256(json.dumps(gate['source_sha256'], sort_keys=True).encode()).hexdigest()
if args.check_only:
    print(json.dumps(dict(host=host, status='local_gate_reviewed', source_digest=source_digest,
                         binding_sha256=binding_hash)))
    raise SystemExit(0)
case = cross / 'runs' / ('-'.join((args.profile, args.scenario, args.direction)) + '01')
case.mkdir(parents=True, exist_ok=True)
out = case / args.mode
assert not out.exists()
command = ['python3', str(root / 'tools/with_resource_delegate.py'), '--native', '--',
           'python3', str(root / 'tools/with_adapter_resources.py'), '--',
           'python3', str(root / 'tests/native/crosshost_revocation.py'), args.mode,
           '--source', str(root), '--build', str(build), '--bindings', str(bindings),
           '--scenario', args.scenario, '--output', str(out)]
if args.mode == 'controller':
    assert host == 'sb16'
    command += ['--address', '10.0.7.49', '--delayed-host',
                'sb16-2' if args.direction == 'forward' else 'sb16']
else:
    bundle = case / ('controller/private-bundle' if host == 'sb16' else 'private-bundle')
    assert bundle.is_dir()
    command += ['--bundle', str(bundle)]
env = {**os.environ, 'PYTHONPATH': str(build / 'python') + ':' + str(root),
       'SPATIAL_TEST_XSERVER': profile['launcher'], 'SPATIAL_SECURITY_EVIDENCE': str(out / 'native'),
       'SPATIAL_SCOPE_EVIDENCE': str(out / 'scopes'), 'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1',
       'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}
record = dict(status='running', host=host, mode=args.mode, profile=args.profile,
              scenario=args.scenario, direction=args.direction, command=command,
              candidate=gate['candidate'], source_digest=source_digest, binding_sha256=binding_hash,
              gate_sha256=primary['gate_sha256'], runner_pid=os.getpid(), started=time.time())
review = case / (args.mode + '-review.json')
review.write_text(json.dumps(record, indent=2) + '\n')
try:
    with (case / (args.mode + '.log')).open('wb') as log:
        result = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    record['exit_code'] = result.returncode
    if (out / 'result.json').exists():
        record['result'] = json.loads((out / 'result.json').read_text())
    record['status'] = 'passed' if result.returncode == 0 and record.get('result', {}).get('status') == 'passed' else 'failed'
except BaseException as error:
    record.update(status='failed', error=repr(error))
    raise
finally:
    record['ended'] = time.time()
    review.write_text(json.dumps(record, indent=2) + '\n')
print(record['status'], flush=True)
raise SystemExit(0 if record['status'] == 'passed' else 1)
