"""Build the unchanged production paths and execute three connected native cases."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import tarfile
import time

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-operator-native-01'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
archive = base / 'operator-native-source-01.tar'
assert sha(archive) == sys.argv[1]
root.mkdir(exist_ok=False)
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({member.name for member in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        path = root.joinpath(*name.parts)
        assert path.resolve().is_relative_to(root.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
manifest = json.loads((root / 'manifest.json').read_bytes())
source = root / 'source'
server = base / 'xlibre-patched/96cd5ea7cdca292239c98d2c3c7cbd41cc37964d13b4bdc5f2d4159aa751af5b/source/build/hw/vfb/Xvfb'
server_hash = '5fd994bd4890340c7e07a20f3b9905f68c875ef4b1c7812fdb713765c75ad107'

def audit():
    assert all(sha(source / name) == digest for name, digest in manifest['files'].items())
    assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
    assert sha(server) == server_hash
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try: name = (path / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        assert name not in ('ctest', 'ninja', 'cc1', 'cc1plus', 'clang', 'clang++'), (path, name)

for profile in ('debug', 'runtime-sanitize'):
    out = root / 'checks' / profile; out.mkdir(parents=True, exist_ok=False)
    build = root / profile / 'build'
    env = {name: value for name, value in os.environ.items() if name != 'LSAN_OPTIONS'}
    env.update(CCACHE_BASEDIR=str(root), SPATIAL_TEST_XSERVER=str(server))
    record = dict(profile=profile, host='sb16', status='running', started=time.time(), commands=[],
        source=manifest['files'], server=dict(path=str(server), sha256=server_hash))
    def save(): (out / 'review.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())
    targets = ['spatial-session-application-probe', 'worldd', 'spatialwm', 'workspaced', 'spatial-app-init',
        'spatial-confined-xprobe', 'spatial-authority-launch-probe', 'spatial-security-auth',
        'spatial-authority-supervisor', 'spatial-participant-runtime-probe', 'identityd', 'spatial-issuer-client',
        'spatial-revoke', 'session-probe-python', 'broker-python-protobuf', 'world-session-python-protobuf']
    commands = [
        ('configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Debug',
            '-DSPATIAL_INPUT_TESTS=ON', '-DSPATIAL_SANITIZERS=' + ('ON' if profile == 'runtime-sanitize' else 'OFF'),
            '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache']),
        ('build', ['cmake', '--build', str(build), '--parallel', '2', '--target'] + targets),
        ('native', ['systemd-run', '--user', '--wait', '--pipe', '--collect',
            '--unit=spatial-operator-native-01-' + profile, '--property=Delegate=yes',
            '--property=RuntimeMaxSec=180', '--property=TimeoutStopSec=20',
            '--setenv=PYTHONPATH=' + str(build / 'python') + ':' + str(source),
            '--setenv=SPATIAL_TEST_XSERVER=' + str(server),
            '--setenv=SPATIAL_SECURITY_EVIDENCE=' + str(out / 'native'),
            '--setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
            '--setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
            '/usr/bin/python3', str(source / 'tests/native/test_operator_revocation.py'), str(source),
            str(build / 'spatial-authority-launch-probe'), str(build / 'spatial-security-auth'),
            str(build / 'identityd'), str(build / 'spatial-issuer-client')])]
    save()
    try:
        for label, command in commands:
            audit()
            item = dict(label=label, argv=command, started=time.time())
            record['commands'].append(item); save()
            with (out / (label + '.log')).open('xb') as log:
                result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
            item.update(exit_code=result.returncode, ended=time.time(), sha256=sha(out / (label + '.log')))
            if label == 'build':
                record['binaries'] = {path.name: sha(path) for path in build.iterdir() if path.is_file() and os.access(path, os.X_OK)}
            save(); print(profile, label, result.returncode, flush=True)
            if result.returncode:
                print((out / (label + '.log')).read_text()[-14000:], flush=True)
                raise RuntimeError(label + ' failed')
        audit()
        assert all(sha(build / name) == digest for name, digest in record['binaries'].items())
        text = (out / 'native.log').read_text()
        assert 'Ran 3 tests' in text and '\nOK\n' in text and 'skipped=' not in text
        record.update(status='passed', cases=3)
    except BaseException:
        record['status'] = 'failed'
        raise
    finally:
        record['ended'] = time.time(); save()
print('Both profiles passed three native operator cases.', flush=True)
