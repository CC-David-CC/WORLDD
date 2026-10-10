"""Launch one bounded component of the coordinated native two-host check."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('mode', choices=('controller', 'worker'))
p.add_argument('profile', choices=('debug', 'runtime-sanitize'))
p.add_argument('run', choices=('forward01', 'reverse01', 'forward02', 'reverse02'))
p.add_argument('--address')
p.add_argument('--delayed-host', choices=('sb16', 'sb16-2'))
p.add_argument('--source-tag', choices=('01', '02', '03'), default='01')
args = p.parse_args()
host = socket.gethostname().split('.')[0]; assert host in ('sb16', 'sb16-2')
root = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m02-crosshost-cancellation-01')
source_root = root.with_name('m02-crosshost-cancellation-' + args.source_tag)
source = source_root / 'source'; build = root / args.profile / 'build'
manifest = json.loads((source_root / 'manifest.json').read_bytes())
build_record = json.loads((root / 'checks' / args.profile / 'build-review.json').read_bytes())
assert build_record['status'] == 'built_tests_unrun'
assert set(manifest['files']) == set(build_record['source'])
assert {name for name in manifest['files'] if manifest['files'][name] != build_record['source'][name]} <= {
    'tests/native/crosshost_revocation.py'}
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
def audit():
    assert all(sha(source / name) == digest for name, digest in manifest['files'].items())
    assert all(sha(root / 'source' / name) == digest for name, digest in build_record['source'].items())
    assert all(sha(build / name) == digest for name, digest in build_record['binaries'].items())
    assert sha(Path(build_record['server']['path'])) == build_record['server']['sha256']
audit()
for path in Path('/proc').iterdir():
    if not path.name.isdigit(): continue
    try: name = (path / 'comm').read_text().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError): continue
    assert name not in ('ctest', 'cc1', 'cc1plus', 'clang', 'clang++', 'ninja'), (path, name)
run = source_root / 'runs' / (args.profile + '-' + args.run); run.mkdir(mode=0o700, parents=True, exist_ok=True)
output = run / args.mode
record_path = run / (args.mode + '-review.json'); assert not record_path.exists() and not output.exists()
command = ['systemd-run', '--user', '--wait', '--pipe', '--collect',
    '--unit=spatial-crosshost' + args.source_tag + '-' + args.profile + '-' + args.run + '-' + args.mode,
    '--property=Delegate=yes', '--property=RuntimeMaxSec=240', '--property=TimeoutStopSec=20',
    '--setenv=PYTHONPATH=' + str(build / 'python') + ':' + str(source),
    '--setenv=SPATIAL_TEST_XSERVER=' + build_record['server']['path'],
    '--setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
    '--setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
    '/usr/bin/python3', str(source / 'tests/native/crosshost_revocation.py'), args.mode,
    '--source', str(source), '--build', str(build), '--output', str(output)]
if args.mode == 'controller':
    assert args.address and args.delayed_host
    command += ['--address', args.address, '--delayed-host', args.delayed_host]
else:
    assert not args.address and not args.delayed_host
    command += ['--bundle', str(run / 'private-bundle')]
record = dict(status='running', host=host, profile=args.profile, mode=args.mode, run=args.run,
    source_tag=args.source_tag, source_manifest_sha256=sha(source_root / 'manifest.json'),
    command=command, started=time.time(), build_review_sha256=sha(root / 'checks' / args.profile / 'build-review.json'))
def save(): record_path.write_bytes((json.dumps(record, indent=2) + '\n').encode())
save()
try:
    with (run / (args.mode + '.log')).open('xb') as log:
        result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT,
            env={name: value for name, value in os.environ.items() if name != 'LSAN_OPTIONS'}, timeout=275)
    record['exit_code'] = result.returncode
    audit()
    record['status'] = 'passed' if result.returncode == 0 else 'failed'
    print(host, args.mode, args.profile, result.returncode, flush=True)
    if result.returncode: print((run / (args.mode + '.log')).read_text()[-8000:], flush=True)
except BaseException as error:
    record['status'] = 'launcher_error'; record['error'] = type(error).__name__ + ': ' + str(error)
    raise
finally:
    record['ended'] = time.time()
    record['log_sha256'] = sha(run / (args.mode + '.log'))
    save()
raise SystemExit(record['exit_code'])
