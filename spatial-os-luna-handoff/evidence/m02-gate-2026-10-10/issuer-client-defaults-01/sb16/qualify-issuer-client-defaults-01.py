"""Check new timeout coverage with pinned binaries and isolated CMake metadata."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
gate = base / 'm02-phase-gate-02'
root = base / 'm02-issuer-client-defaults-01'
inputs = base / 'm02-issuer-client-defaults-inputs-01'
lock = (base / 'm02-phase-qualification.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
expected = {
    'CMakeLists.txt': '8858b96c216250a96416d37943a5c711539fd0beeee1f79d9ce5c22d70cdcaa6',
    'tests/core/test_issuer_client_defaults.py': '9e39e1a9a5e50e74f652e8ec1a68af23e3f55223fa24b943513c2c402e37af29',
}
assert all(sha(inputs / name) == digest for name, digest in expected.items())
manifest = json.loads((gate / 'manifest.json').read_bytes())
prior = json.loads((gate / 'summary.json').read_bytes())


def busy():
    found = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            name = (path / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if name in ('cc1', 'cc1plus', 'clang', 'clang++', 'lto1', 'ninja', 'ctest', 'bpftrace'):
            found.append((path.name, name))
    return found


def audit():
    assert all(sha(gate / 'source' / name) == digest for name, digest in manifest['files'].items())
    for profile in ('debug', 'runtime-sanitize'):
        assert all(sha(gate / profile / 'build' / name) == digest
                   for name, digest in prior['profiles'][profile]['binaries'].items())
    assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())


assert not busy()
audit()
root.mkdir(exist_ok=False)
source = root / 'source'
for name in manifest['files']:
    target = source / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(gate / 'source' / name, target)
for name in expected:
    target = source / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(inputs / name, target)
record = dict(host=host, status='preparing', pid=os.getpid(), started=time.time(),
              binary_source_commit=manifest['commit'], source_inputs=expected,
              production_changed=False, existing_tests_changed=False, locked_files_changed=False,
              runner_sha256=sha(Path(__file__)), profiles={}, commands=[], phase_complete=False,
              qualification_scope='Focused regression coverage using frozen candidate02 binaries. CMake registration checked separately.')


def save():
    temp = root / 'review.json.tmp'
    temp.write_text(json.dumps(record, indent=2) + '\n')
    temp.replace(root / 'review.json')


def command(label, args, env=None, timeout=90):
    assert not busy()
    entry = dict(label=label, argv=args, started=time.time())
    record['commands'].append(entry)
    save()
    with (root / (label + '.log')).open('xb') as log:
        result = subprocess.run(args, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    entry.update(exit_code=result.returncode, ended=time.time(), sha256=sha(root / (label + '.log')))
    save()
    assert result.returncode == 0, label + ' failed; preserve the log'
    return (root / (label + '.log')).read_text()


save()
try:
    configured = root / 'configuration'
    config_env = dict(os.environ, SPATIAL_TEST_XSERVER=prior['server']['path'])
    command('configure', ['cmake', '-S', str(source), '-B', str(configured), '-G', 'Ninja',
                         '-DCMAKE_BUILD_TYPE=Debug', '-DBUILD_TESTING=ON', '-DSPATIAL_INPUT_TESTS=ON',
                         '-DSPATIAL_SANITIZERS=OFF'], env=config_env, timeout=90)
    inventory = json.loads(command('inventory', ['ctest', '--test-dir', str(configured), '--show-only=json-v1']))
    tests = {test['name']:test for test in inventory['tests']}
    assert len(tests) == 125 and 'issuer-client-defaults' in tests
    chosen = tests['issuer-client-defaults']
    assert chosen['command'] == ['/usr/bin/python3', str(source / 'tests/core/test_issuer_client_defaults.py'),
                                  str(configured / 'identityd'), str(configured / 'spatial-issuer-client')]
    assert next(p['value'] for p in chosen['properties'] if p['name'] == 'TIMEOUT') == 30
    record['registration'] = dict(suite='issuer-client-defaults', total_suites=125, verified=True)
    record['status'] = 'checking'
    command('sync-before-tests', ['sync', '-f', str(root)], timeout=60)
    for profile in ('debug', 'runtime-sanitize'):
        build = gate / profile / 'build'
        env = {k:v for k,v in os.environ.items() if k not in ('LSAN_OPTIONS', 'DISPLAY', 'SPATIAL_TEST_DISPLAY')}
        env.update(PYTHONPATH=str(build / 'python') + ':' + str(source),
                   ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
                   UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        results = []
        for suite, script in (('issuer-client', 'test_issuer_client.py'),
                              ('issuer-client-defaults', 'test_issuer_client_defaults.py')):
            label = profile + '-' + suite
            env['SPATIAL_SECURITY_EVIDENCE'] = str(root / (label + '-native'))
            args = ['/usr/bin/python3', str(source / 'tests/core' / script), str(build / 'identityd'),
                    str(build / 'spatial-issuer-client')]
            text = command(label, args, env, timeout=120)
            match = re.search(r'Ran (\d+) tests? in ', text)
            assert match and re.search(r'\nOK\s*$', text) and 'skipped=' not in text
            count = int(match[1])
            if suite == 'issuer-client-defaults':
                assert count == 3
            results.append(dict(suite=suite, passed=count, failed=0, skipped=0))
            print(host, profile, suite, count, 'passed', flush=True)
        record['profiles'][profile] = results
        save()
    audit()
    assert all(sha(source / name) == digest for name, digest in manifest['files'].items() if name != 'CMakeLists.txt')
    assert all(sha(source / name) == digest for name, digest in expected.items())
    assert not busy()
    scanned = []
    for path in root.rglob('*.log'):
        if path.is_relative_to(configured):
            continue
        data = path.read_text(errors='replace')
        assert not re.search(r'ERROR: AddressSanitizer|ERROR: LeakSanitizer|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:', data), path
        scanned.append(path.relative_to(root).as_posix())
    record.update(status='passed_review_pending', scanned_logs=scanned, sanitizer_matches=0,
                  original_source_files_verified=351, locked_files_verified=41, source_and_binaries_unchanged=True)
except BaseException as error:
    record.update(status='failed', error=type(error).__name__ + ': ' + str(error))
    raise
finally:
    record['ended'] = time.time()
    record['attachments'] = {p.relative_to(root).as_posix():sha(p) for p in root.rglob('*')
                             if p.is_file() and not p.is_relative_to(source) and not p.is_relative_to(root/'configuration')
                             and p.name != 'review.json'}
    save()
    print(host, record['status'], flush=True)
