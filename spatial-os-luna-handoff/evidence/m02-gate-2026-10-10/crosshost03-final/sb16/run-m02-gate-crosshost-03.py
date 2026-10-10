"""Run a cross-host witness only after the frozen local gate is reviewed."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from m02_crosshost_qualification03 import load_qualification

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=('controller', 'worker'))
parser.add_argument('profile', choices=('debug', 'runtime-sanitize'))
parser.add_argument('scenario', choices=('delay', 'crash'))
parser.add_argument('direction', choices=('forward', 'reverse'))
parser.add_argument('--qualification', required=True, choices=('m02-phase-gate-02', 'm02-untraced-03'))
parser.add_argument('--check-only', action='store_true')
args = parser.parse_args()
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
os.umask(0o077)
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
gate_lock = (base/'m02-phase-qualification.lock').open('a')
fcntl.flock(gate_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
gate = base/'m02-phase-gate-02'
source = gate/'source'
build = gate/args.profile/'build'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest, summary, review, qualification = load_qualification(base, host, args.qualification)
controller = 'sb16' if args.direction == 'forward' else 'sb16-2'
delayed = 'sb16-2' if controller == 'sb16' else 'sb16'
address = {'sb16':'10.0.7.49', 'sb16-2':'10.0.7.94'}[controller]
assert args.mode != 'controller' or host == controller
server = summary['server']

def audit():
    assert sha(base/args.qualification/'summary.json') == qualification['summary_sha256']
    assert sha(base/args.qualification/'primary-review.json') == qualification['review_sha256']
    assert all(sha(source/name) == digest for name, digest in manifest['files'].items())
    assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
    assert all(sha(build/name) == digest for name, digest in summary['profiles'][args.profile]['binaries'].items())
    assert sha(Path(server['path'])) == server['sha256']

audit()
for process in Path('/proc').iterdir():
    if not process.name.isdigit():
        continue
    try:
        name = (process/'comm').read_text().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        continue
    assert name not in ('ctest', 'ninja', 'cc1', 'cc1plus', 'clang', 'clang++', 'lto1', 'bpftrace'), (process, name)
if args.check_only:
    print(json.dumps(dict(host=host, status='local_gate_reviewed', commit=manifest['commit'],
        profile=args.profile, qualification=qualification)))
    raise SystemExit(0)
root = base/'m02-gate-crosshost-03'
root.mkdir(mode=0o700, exist_ok=True)
with (root/'bindings.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    bindings = root/'python'
    bindings_record = root/'bindings-review.json'
    if not bindings_record.exists():
        bindings.mkdir(exist_ok=False)
        command = ['protoc', '--python_out='+str(bindings)]
        command += ['--proto_path='+str(source/name) for name in ('tests/proto', 'proto/spatial/workspace/v1',
            'proto/spatial/supervisor/v1', 'proto/spatial/security/v1', 'proto/spatial/identity/v1',
            'proto/spatial/authority/v1', 'proto/spatial/core/v1')]
        command += [str(source/'tests/proto/crosshost_revocation.proto')]
        result = subprocess.run(command, capture_output=True, timeout=30)
        (root/'bindings.log').write_bytes(result.stdout+result.stderr)
        assert result.returncode == 0
        bindings_record.write_bytes((json.dumps(dict(command=command, exit_code=0,
            schema_sha256=manifest['files']['tests/proto/crosshost_revocation.proto'],
            binding_sha256=sha(bindings/'crosshost_revocation_pb2.py')), indent=2)+'\n').encode())
    binding = json.loads(bindings_record.read_bytes())
    assert binding['exit_code'] == 0
    assert binding['schema_sha256'] == manifest['files']['tests/proto/crosshost_revocation.proto']
    assert sha(bindings/'crosshost_revocation_pb2.py') == binding['binding_sha256']
run_name = args.profile+'-'+args.scenario+'-'+args.direction+'01'
run = root/'runs'/run_name
run.mkdir(mode=0o700, parents=True, exist_ok=True)
output = run/args.mode
record_path = run/(args.mode+'-review.json')
assert not output.exists()
command = ['systemd-run', '--user', '--wait', '--pipe', '--collect',
    '--unit=spatial-m02-gate-crosshost03-'+run_name+'-'+args.mode,
    '--property=Delegate=yes', '--property=RuntimeMaxSec=240', '--property=TimeoutStopSec=20',
    '--setenv=PYTHONPATH='+str(build/'python')+':'+str(source),
    '--setenv=SPATIAL_TEST_XSERVER='+server['path'],
    '--setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
    '--setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
    '/usr/bin/python3', str(source/'tests/native/crosshost_revocation.py'), args.mode,
    '--source', str(source), '--build', str(build), '--bindings', str(bindings),
    '--scenario', args.scenario, '--output', str(output)]
if args.mode == 'controller':
    command += ['--address', address, '--delayed-host', delayed]
else:
    command += ['--bundle', str(run/'private-bundle')]
record = dict(status='running', runner_pid=os.getpid(), runner_sha256=sha(Path(__file__)), host=host, profile=args.profile, scenario=args.scenario,
    direction=args.direction, mode=args.mode, command=command, started=time.time(),
    source_manifest_sha256=sha(gate/'manifest.json'), qualification=qualification,
    qualification_helper_sha256=sha(base/'m02_crosshost_qualification03.py'),
    bindings_review_sha256=sha(bindings_record), server=server, commit=manifest['commit'])
with record_path.open('xb') as stream:
    stream.write((json.dumps(record, indent=2)+'\n').encode())
try:
    with (run/(args.mode+'.log')).open('xb') as log:
        result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT,
            env={name:value for name,value in os.environ.items() if name!='LSAN_OPTIONS'}, timeout=275)
    record['exit_code'] = result.returncode
    audit()
    record['status'] = 'passed' if result.returncode == 0 else 'failed'
    print(host, args.mode, run_name, result.returncode, flush=True)
    if result.returncode:
        print((run/(args.mode+'.log')).read_text()[-10000:], flush=True)
except BaseException as error:
    record.update(status='launcher_error', error=type(error).__name__+': '+str(error))
    raise
finally:
    record.update(ended=time.time(), log_sha256=sha(run/(args.mode+'.log')))
    record_path.write_bytes((json.dumps(record, indent=2)+'\n').encode())
raise SystemExit(record['exit_code'])
