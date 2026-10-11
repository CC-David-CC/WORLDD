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
root = base/'m03-broker-resources-04'; source = root/'source'
lock = (base/'m02-phase-qualification.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
assert not (root/'review.json').exists(), 'Preserve every recorded attempt'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest = json.loads((root/'manifest.json').read_bytes())
previous = base/'m03-broker-resources-02'
prior_manifest = json.loads((previous/'manifest.json').read_bytes())
prior_review = json.loads((previous/'review.json').read_bytes())
assert prior_review['status']=='passed_review_pending'
for name,value in manifest['files'].items():
    if name.startswith(('src/', 'proto/')) and name!='src/testing/process_termination_probe.cpp':
        assert value==prior_manifest['files'][name], name
recorded_prior_sha = sha(previous/'review.json')
termination = base/'m03-broker-resources-03'
termination_review = json.loads((termination/'review.json').read_bytes())
assert 'ended' in termination_review
termination_manifest = json.loads((termination/'manifest.json').read_bytes())
for name in ('src/testing/process_termination_probe.cpp','tests/core/test_process_termination.py','CMakeLists.txt'):
    assert manifest['files'][name]==termination_manifest['files'][name], name
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
        previous_checks = termination/(profile+'-checks.log')
        retained = next((item for item in termination_review['commands'] if item['label']==profile+'-checks' and item.get('exit_code')==0), None)
        if retained:
            assert sha(previous_checks)==retained['sha256']
            assert re.findall(r'Ran (\d+) tests? in ', previous_checks.read_text())==['6']
        build = (termination if retained else root)/profile/'build'
        env = dict(os.environ)
        for key in ('DISPLAY', 'SPATIAL_TEST_DISPLAY', 'LSAN_OPTIONS'): env.pop(key, None)
        env.update(PYTHONPATH=str(build/'python')+':'+str(source),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',
            SPATIAL_SCOPE_EVIDENCE=str(root/profile/'witnesses'))
        if not retained:
            run(profile+'-configure', ['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug',
                '-DBUILD_TESTING=ON','-DSPATIAL_INPUT_TESTS=OFF',
                '-DSPATIAL_SANITIZERS='+('ON' if profile=='runtime-sanitize' else 'OFF')], env, timeout=90)
            run(profile+'-build', ['cmake','--build',str(build),'--target','spatial-process-termination-probe','--parallel','2'], env, timeout=600)
            run(profile+'-sync', ['sync','-f',str(root)], timeout=60)
            text = run(profile+'-checks', ['ctest','--test-dir',str(build),'-V','--output-on-failure','-R',
                '^process-termination$'], env, timeout=300)
            counts = [int(x) for x in re.findall(r'Ran (\d+) tests? in ', text)]
            assert counts==[6] and '100% tests passed' in text and 'skipped=' not in text
        old_build = previous/profile/'build'
        for name in ('workspaced','spatial-process-scope-probe'):
            assert sha(old_build/name)==prior_review['profiles'][profile]['binaries'][name]
        io_text = run(profile+'-application-io', ['python3',str(source/'tools/with_resource_delegate.py'),'--','python3',
            str(source/'tests/core/test_broker_resources.py'),str(old_build/'spatial-process-scope-probe'),str(old_build/'workspaced'),
            'BrokerResourceTests.test_application_writes_obey_broker_io_budget'], env)
        assert re.findall(r'Ran (\d+) tests? in ', io_text)==['1'] and '\nOK\n' in io_text
        record['profiles'][profile] = dict(passed=1+(0 if retained else 6), retained_termination_checks=6 if retained else 0,
            termination_log=str(previous_checks) if retained else str(root/(profile+'-checks.log')), failed=0, skipped=0,
            probe_sha256=sha(build/'spatial-process-termination-probe'),
            reused_binaries={name:sha(old_build/name) for name in ('workspaced','spatial-process-scope-probe')},
            prior_review_sha256=recorded_prior_sha)
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
