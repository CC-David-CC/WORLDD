"""Qualify the frozen broker handoff after this host's unchanged-source baseline."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
baseline = json.loads((r / 'local/supervisor-broker-handoff-baseline-01/summary.json').read_text())
assert baseline['host'] == host and baseline['source_files'] == 281 and not baseline['production_changed']
for profile in ('debug', 'runtime-sanitize'):
    assert baseline['profiles'][profile]['expected_missing_handoff_failures'] == 1
    text = (r / 'local/supervisor-broker-handoff-baseline-01' / profile / 'test.log').read_text()
    assert 'has no field named "broker_native_target_fd"' in text and 'FAILED (failures=1)' in text
for p in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        if p.parent.stat().st_uid != os.geteuid():
            continue
        args = p.read_bytes().split(b'\0')
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        continue
    if len(args) >= 2 and Path(os.fsdecode(args[0])).name.startswith('python'):
        assert Path(os.fsdecode(args[1])).name not in (
            'qualify-authority-supervisor-02.py', 'finish-authority-supervisor-full-02.py',
            'after-supervisor-full-02.py', 'diagnose-supervisor-broker-handoff-01.py',
            'after-supervisor-broker-baseline-01.py'), 'Preceding qualification or baseline remains active'
out = r / 'local/m02-supervisor-broker-handoff-01'
source = out / 'source'
source.mkdir(parents=True)
with tarfile.open(r / 'local/supervisor-broker-handoff-candidate-01.tar') as archive:
    archive.extractall(source, filter='data')
manifest = json.loads((r / 'local/supervisor-broker-handoff-candidate-01.json').read_text())['files']
assert len(manifest) == 283
def audit():
    for files in (manifest, json.loads((source / 'acceptance.lock.json').read_text())['files']):
        for name, digest in files.items():
            assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
audit()
server = json.loads((r / 'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest() == server['sha256']
pattern = '^(authority-(supervisor|native-config|participant-relay|owned-participant|launch-coordinator|prepared-launch|process-scope|native-scope)|native-application-launch)$'
for profile, sanitize in (('debug', 'OFF'), ('runtime-sanitize', 'ON')):
    target = out / profile
    target.mkdir()
    build = target / 'build'
    env = dict(os.environ, SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build / 'spatial-xprobe'),
        SPATIAL_SECURITY_EVIDENCE=str(target / 'server'), PYTHONPATH=str(build / 'python') + ':' + str(source),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands = [
        ('configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Debug',
            '-DSPATIAL_INPUT_TESTS=ON', '-DSPATIAL_SANITIZERS=' + sanitize]),
        ('build', ['cmake', '--build', str(build), '--parallel', '2']),
        ('harness', ['python3', '-m', 'unittest', 'discover', '-s', 'tests/harness', '-t', '.', '-q']),
        ('handoff', ['ctest', '--test-dir', str(build), '-V', '-R', '^authority-broker-handoff$']),
        ('regressions', ['ctest', '--test-dir', str(build), '-V', '-R', '^authority-broker-handoff-regressions$']),
        ('focused', ['ctest', '--test-dir', str(build), '-V', '-R', pattern]),
    ]
    results = []
    for name, command in commands:
        with (target / (name + '.log')).open('wb') as log:
            run = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
        results.append(dict(name=name, command=command, exit_code=run.returncode))
        (target / 'run.json').write_text(json.dumps(dict(host=host, source_files=283, results=results, server=server), indent=2) + '\n')
        print(host, profile, name, run.returncode, flush=True)
        if run.returncode:
            print((target / (name + '.log')).read_text()[-16000:], flush=True)
            raise SystemExit(run.returncode)
    audit()
print(host, 'broker handoff focused qualification complete', flush=True)
