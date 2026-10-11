"""Qualify an immutable broker resource candidate on an authorized Linux host."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base/'m03-broker-resources-01'; source = root/'source'
lock = (base/'m02-phase-qualification.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
assert not (root/'review.json').exists(), 'Preserve every recorded attempt'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest = json.loads((root/'manifest.json').read_bytes())
assert all(sha(source/name) == value for name,value in manifest['files'].items())
assert all(manifest['files'][name] == value for name,value in manifest['locked_files'].items())


def busy():
    result = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try: name = (path/'comm').read_text().strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError): continue
        if name in ('ninja', 'ctest', 'cc1', 'cc1plus', 'clang', 'clang++', 'bpftrace'):
            result.append((path.name, name))
    return result


assert not busy(), busy()
record = dict(host=host, status='building', started=time.time(), pid=os.getpid(),
    manifest_sha256=sha(root/'manifest.json'), runner_sha256=sha(Path(__file__)), commands=[], profiles={},
    phase_complete=False, scope='Broker resource integration. Launcher provisioning and storage remain pending.')


def save():
    path = root/'review.json.tmp'; path.write_text(json.dumps(record, indent=2)+'\n'); path.replace(root/'review.json')


def run(label, command, env=None, expected=0, timeout=180):
    assert not busy(), busy()
    item = dict(label=label, argv=command, started=time.time()); record['commands'].append(item); save()
    with (root/(label+'.log')).open('xb') as log:
        result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    item.update(exit_code=result.returncode, ended=time.time(), sha256=sha(root/(label+'.log'))); save()
    text = (root/(label+'.log')).read_text()
    if result.returncode != expected:
        print('\n'.join(text.splitlines()[-85:]), flush=True)
        raise RuntimeError(label+' failed; preserve this attempt')
    return text


save()
try:
    for profile in ('debug', 'runtime-sanitize'):
        build = root/profile/'build'
        env = dict(os.environ)
        for key in ('DISPLAY', 'SPATIAL_TEST_DISPLAY', 'LSAN_OPTIONS'): env.pop(key, None)
        env.update(PYTHONPATH=str(build/'python')+':'+str(source),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',
            SPATIAL_SCOPE_EVIDENCE=str(root/profile/'witnesses'))
        run(profile+'-configure', ['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug',
            '-DBUILD_TESTING=ON','-DSPATIAL_INPUT_TESTS=OFF',
            '-DSPATIAL_SANITIZERS='+('ON' if profile=='runtime-sanitize' else 'OFF')], env, timeout=90)
        run(profile+'-build', ['cmake','--build',str(build),'--target','workspaced','spatial-process-scope-probe',
            'spatial-process-termination-probe','--parallel','2'], env, timeout=600)
        run(profile+'-sync', ['sync','-f',str(root)], timeout=60)
        if profile == 'debug':
            old = base/'m02-phase-gate-02/debug/build/workspaced'
            text = run('baseline', ['python3',str(source/'tools/with_resource_delegate.py'),'--','python3',
                str(source/'tests/core/test_broker_resources.py'),str(build/'spatial-process-scope-probe'),str(old),
                'BrokerResourceTests.test_missing_configuration_denies_before_pending_record'],
                dict(env, SPATIAL_SCOPE_EVIDENCE=str(root/'baseline-witnesses')), expected=1)
            assert 'FAIL: test_missing_configuration_denies_before_pending_record' in text
            assert 'FAILED (failures=1)' in text and 'launched {' in text
            record['baseline'] = dict(status='expected_unbudgeted_launch_failure', binary_sha256=sha(old)); save()
        text = run(profile+'-checks', ['ctest','--test-dir',str(build),'-V','--output-on-failure','-R',
            '^(broker-resources|process-scope-resources|authority-process-scope|authority-prepared-launch)$'], env, timeout=300)
        counts = [int(x) for x in re.findall(r'Ran (\d+) tests? in ', text)]
        assert len(counts)==4 and '100% tests passed' in text and 'skipped=' not in text
        record['profiles'][profile] = dict(passed=sum(counts), suite_counts=counts, failed=0, skipped=0,
            binaries={name:sha(build/name) for name in ('workspaced','spatial-process-scope-probe','spatial-process-termination-probe','spatial-app-init')})
        print(host, profile, record['profiles'][profile], flush=True); save()
    assert all(sha(source/name)==value for name,value in manifest['files'].items())
    logs = [path for path in root.rglob('*.log') if 'build' not in path.parts and 'source' not in path.parts]
    for path in logs:
        assert not re.search(r'ERROR: AddressSanitizer|ERROR: LeakSanitizer|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:', path.read_text(errors='replace')), str(path)
    record.update(status='passed_review_pending', sanitizer_logs_scanned=len(logs), sanitizer_matches=0)
except BaseException as error:
    record.update(status='failed', error=type(error).__name__+': '+str(error)); raise
finally:
    record['ended'] = time.time()
    record['attachments'] = {path.relative_to(root).as_posix():sha(path) for path in root.rglob('*')
        if path.is_file() and 'source' not in path.relative_to(root).parts and 'build' not in path.relative_to(root).parts
        and path.name!='review.json'}
    save(); print(host, record['status'], flush=True)
