"""Build one focused profile on an allowed host with two compiler jobs."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

host = socket.gethostname().split('.')[0]; assert host in ('sb16', 'sb16-2')
profile = sys.argv[1]; assert profile in ('debug', 'runtime-sanitize')
root = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m02-crosshost-cancellation-01')
source = root / 'source'; build = root / profile / 'build'
out = root / 'checks' / profile; out.mkdir(parents=True, exist_ok=False)
manifest = json.loads((root / 'manifest.json').read_bytes())
server = dict(path='/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/xlibre-patched/96cd5ea7cdca292239c98d2c3c7cbd41cc37964d13b4bdc5f2d4159aa751af5b/source/build/hw/vfb/Xvfb',
    sha256='5fd994bd4890340c7e07a20f3b9905f68c875ef4b1c7812fdb713765c75ad107')
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
record = dict(status='building', started=time.time(), host=host, profile=profile,
    source=manifest['files'], locked=manifest['locked_files'], server=server, commands=[])

def save():
    (out / 'build-review.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())

def audit():
    assert all(sha(source / name) == value for name, value in manifest['files'].items())
    assert sha(Path(server['path'])) == server['sha256']
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try: name = (path / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        assert name not in ('ctest', 'cc1', 'cc1plus', 'clang', 'clang++', 'ninja'), (path, name)

env = dict(os.environ, CCACHE_BASEDIR=str(root), SPATIAL_TEST_XSERVER=server['path'])
save()
try:
    commands = [
        ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Debug',
            '-DSPATIAL_INPUT_TESTS=ON', '-DSPATIAL_SANITIZERS=' + ('ON' if profile == 'runtime-sanitize' else 'OFF'),
            '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache'],
        ['cmake', '--build', str(build), '--parallel', '2', '--target',
            'spatial-session-application-probe', 'worldd', 'spatialwm', 'workspaced', 'spatial-app-init',
            'spatial-confined-xprobe', 'spatial-authority-launch-probe', 'spatial-security-auth',
            'spatial-authority-supervisor', 'spatial-participant-runtime-probe', 'identityd', 'spatial-issuer-client',
            'crosshost-revocation-python-protobuf', 'session-probe-python', 'broker-python-protobuf',
            'world-session-python-protobuf']]
    for label, command in zip(('configure', 'build'), commands):
        audit(); item=dict(label=label, command=command, started=time.time()); record['commands'].append(item); save()
        with (out / (label + '.log')).open('xb') as log:
            result = subprocess.run(command, env=env, cwd=source, stdout=log, stderr=subprocess.STDOUT)
        item.update(exit_code=result.returncode, ended=time.time(), log_sha256=sha(out / (label + '.log'))); save()
        print(host, profile, label, result.returncode, flush=True)
        if result.returncode:
            print((out / (label + '.log')).read_text()[-6000:], flush=True)
            raise RuntimeError(label + ' failed')
    audit()
    record['binaries'] = {path.name: sha(path) for path in build.iterdir() if path.is_file() and os.access(path, os.X_OK)}
    record['status'] = 'built_tests_unrun'
except BaseException:
    record['status'] = 'failed'
    raise
finally:
    record['ended'] = time.time(); save()
