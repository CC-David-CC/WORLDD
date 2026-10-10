"""Build the connected development snapshot and run only its affected suites."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] == 'sb16'
root = r / 'local/m02-connected-recovery-03'
source = root / 'source'
out = root / 'checks'
out.mkdir(exist_ok=False)
manifest = json.loads((root / 'manifest.json').read_text())
previous = r / 'local/m02-retained-child-handle-01'
prior = json.loads((previous / 'qualification/summary.json').read_text())
server = prior['server']
selection = '^(authority-control-binding|session-broker|supervisor-child-handle-client|supervisor-child-handle|authority-supervisor|control-administrative-transport|workspace-context)$'
record = dict(status='building', host='sb16', started=time.time(), source=manifest['files'],
    locked=manifest['locked_files'], server=server, profiles={}, selection=selection,
    purpose='World endpoint registration and retained launch ownership; shared broker lifecycle, child handles, control binding, administrative transport, and broker callers.',
    full_qualification='Pending coherent M02 phase gate on both hosts.')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    for group in ('files', 'locked_files'):
        assert all(sha(source / n) == h for n, h in manifest[group].items())
    assert sha(Path(server['path'])) == server['sha256']


def idle():
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) == os.getpid():
            continue
        try:
            name = (p / 'comm').read_text().strip()
            args = (p / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        assert name not in ('cc1', 'cc1plus', 'clang', 'clang++', 'lto1', 'ctest'), (p, name)
        assert not any(Path(a.decode(errors='replace')).name in
            ('qualify-control-admin-01.py', 'qualify-retained-child-handle-01.py') for a in args), p


def save():
    (out / 'review.json').write_text(json.dumps(record, indent=2) + '\n')


def run(label, command, directory, env, expected=0):
    idle(); audit()
    log = directory / (label + '.log')
    entry = dict(command=command, started=time.time())
    record['profiles'].setdefault(directory.name, {})[label] = entry
    save()
    with log.open('xb') as stream:
        result = subprocess.run(command, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT)
    entry.update(exit_code=result.returncode, ended=time.time(), log_sha256=sha(log))
    save()
    print(directory.name, label, result.returncode, flush=True)
    if result.returncode != expected:
        print(log.read_text()[-12000:], flush=True)
        raise RuntimeError(label + ' unexpected result')
    audit()


save()
try:
    for profile in ('debug', 'runtime-sanitize'):
        build = root / profile / 'build'
        target = out / profile; target.mkdir()
        env = dict(os.environ, CCACHE_BASEDIR=str(root), PYTHONPATH=str(build / 'python') + ':' + str(source),
            SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build / 'spatial-xprobe'),
            SPATIAL_SECURITY_EVIDENCE=str(target / 'native'), SPATIAL_EVIDENCE_DIR=str(target / 'cases'),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        env.pop('LSAN_OPTIONS', None)
        run('configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
            '-DCMAKE_BUILD_TYPE=Debug', '-DSPATIAL_INPUT_TESTS=ON',
            '-DSPATIAL_SANITIZERS=' + ('ON' if profile == 'runtime-sanitize' else 'OFF'),
            '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache'], target, env)
        run('build', ['cmake', '--build', str(build), '--parallel', '2'], target, env)
        record['profiles'][profile]['binaries'] = {p.name: sha(p) for p in build.iterdir()
            if p.is_file() and os.access(p, os.X_OK)}
        save()
        record['status'] = 'testing-' + profile; save()
        run('world-registration', ['ctest', '--test-dir', str(build), '-V', '-R', '^session-world$'], target, env)
        run('focused', ['ctest', '--test-dir', str(build), '-V', '-R', selection], target, env)
        for n, h in record['profiles'][profile]['binaries'].items():
            assert sha(build / n) == h
    record['status'] = 'passed'
except BaseException:
    record['status'] = 'failed'
    raise
finally:
    record['ended'] = time.time(); save()
