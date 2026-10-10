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
root = r / 'local/m02-connected-recovery-01'
source = root / 'source'
out = root / 'checks-retry02'
out.mkdir(exist_ok=False)
manifest = json.loads((root / 'manifest.json').read_text())
helper = r / 'local/test_supervisor_child_handle_02.py'
original = json.loads((root / 'checks/review.json').read_text())
assert original['status'] == 'failed'
previous = r / 'local/m02-retained-child-handle-01'
prior = json.loads((previous / 'qualification/summary.json').read_text())
server = prior['server']
selection = '^(retained-child-handle|authority-supervisor|authority-broker-handoff|supervisor-handoff-lifetime|authority-control-binding|control-administrative-transport)$'
record = dict(status='building', host='sb16', started=time.time(), source=manifest['files'],
    locked=manifest['locked_files'], server=server, profiles={}, selection=selection,
    purpose='Private broker handle transfer, process ownership, related supervisor lifecycle, and administrative transport.',
    full_qualification='Pending coherent M02 phase gate on both hosts.',
    fixture_correction='Use native target 39 only in the alias-negative case, leaving target 38 free for the alias mapping.',
    original_review_sha256=hashlib.sha256((root / 'checks/review.json').read_bytes()).hexdigest(),
    helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    for group in ('files', 'locked_files'):
        assert all(sha(source / n) == h for n, h in manifest[group].items())
    assert sha(Path(server['path'])) == server['sha256']
    assert sha(helper) == record['helper_sha256']


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
        env = dict(os.environ, PYTHONPATH=str(build / 'python') + ':' + str(source),
            SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build / 'spatial-xprobe'),
            SPATIAL_SECURITY_EVIDENCE=str(target / 'native'), SPATIAL_EVIDENCE_DIR=str(target / 'cases'),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        env.pop('LSAN_OPTIONS', None)
        if profile == 'debug':
            record['profiles'][profile] = dict(binaries=original['profiles']['debug']['binaries'],
                reused_build='Unchanged production source and binaries from checks/review.json')
            for n, h in record['profiles'][profile]['binaries'].items():
                assert sha(build / n) == h
        else:
            run('configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
                '-DCMAKE_BUILD_TYPE=Debug', '-DSPATIAL_INPUT_TESTS=ON', '-DSPATIAL_SANITIZERS=ON',
                '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache'], target, env)
            run('build', ['cmake', '--build', str(build), '--parallel', '2'], target, env)
            record['profiles'][profile]['binaries'] = {p.name: sha(p) for p in build.iterdir()
                if p.is_file() and os.access(p, os.X_OK)}
            save()
            run('related', ['ctest', '--test-dir', str(build), '-V', '-R', selection], target, env)
        command = ['systemd-run', '--user', '--wait', '--pipe', '--collect',
            '--unit=spatial-child-handle-corrected-02-' + profile, '--property=Delegate=yes',
            '--setenv=PYTHONPATH=' + env['PYTHONPATH'] + ':' + str(source / 'tests/native'),
            '--setenv=SPATIAL_TEST_XSERVER=' + server['path'],
            '--setenv=SPATIAL_SECURITY_EVIDENCE=' + str(target / 'native'),
            '--setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
            '--setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
            '--setenv=LSAN_OPTIONS=', 'python3', str(helper), str(source),
            *[str(build / n) for n in ('spatial-authority-launch-probe', 'spatial-security-auth', 'identityd', 'spatial-issuer-client')]]
        record['status'] = 'testing-' + profile; save()
        run('handle', command, target, env)
        content = (target / 'handle.log').read_text()
        assert 'Ran 8 tests in ' in content and '\nOK\n' in content
        for n, h in record['profiles'][profile]['binaries'].items():
            assert sha(build / n) == h
    record['status'] = 'passed'
except BaseException:
    record['status'] = 'failed'
    raise
finally:
    record['ended'] = time.time(); save()
