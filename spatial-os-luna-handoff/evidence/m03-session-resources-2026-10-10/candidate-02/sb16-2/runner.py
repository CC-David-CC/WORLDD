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
root = base/'m03-session-resources-02'; source = root/'source'
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
server = json.loads((base/'m04-surface-identity-build-01/run.json').read_bytes())['binaries']['Xvfb']
assert sha(Path(server['path'])) == server['sha256']
record = dict(server=server, host=host, status='building', started=time.time(), pid=os.getpid(),
    manifest_sha256=sha(root/'manifest.json'), runner_sha256=sha(Path(__file__)), commands=[], profiles={},
    phase_complete=False, scope='Session resource provisioning. Storage and full M03 gate remain pending.')


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
        prior = base/'m03-session-resources-01'
        build = (prior if profile == 'debug' else root)/profile/'build'
        env = dict(os.environ)
        for key in ('DISPLAY', 'SPATIAL_TEST_DISPLAY', 'LSAN_OPTIONS'): env.pop(key, None)
        env.update(PYTHONPATH=str(build/'python')+':'+str(source),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',
            SPATIAL_SCOPE_EVIDENCE=str(root/profile/'witnesses'), SPATIAL_TEST_XSERVER=server['path'],
            SPATIAL_SECURITY_EVIDENCE=str(root/profile/'native'))
        if profile == 'debug':
            before = json.loads((prior/'manifest.json').read_bytes())
            assert manifest['files'] == before['files'], 'Only the evidence runner changed'
            before_review = json.loads((prior/'review.json').read_bytes())
            assert any(c['label']=='debug-build' and c['exit_code']==0 for c in before_review['commands'])
            assert all(sha(prior/'source'/name)==value for name,value in before['files'].items())
            record['reused_debug_build'] = str(build); save()
        else:
            run(profile+'-configure', ['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug',
                '-DBUILD_TESTING=ON','-DSPATIAL_INPUT_TESTS=OFF',
                '-DSPATIAL_SANITIZERS='+('ON' if profile=='runtime-sanitize' else 'OFF')], env, timeout=90)
            run(profile+'-build', ['cmake','--build',str(build),'--target','workspaced','spatial-process-scope-probe',
                'spatial-process-termination-probe', 'spatial-authority-supervisor', 'spatial-authority-launch-probe',
                'spatial-session-application-probe', 'spatial-security-auth', 'identityd', 'spatial-issuer-client',
                'spatial-participant-runtime-probe', 'worldd', 'spatialwm', 'spatial-confined-xprobe', '--parallel','2'], env, timeout=600)
        run(profile+'-sync', ['sync','-f',str(root)], timeout=60)
        if profile == 'debug':
            old = base/'m02-phase-gate-02/debug/build/spatial-authority-supervisor'
            text = run('baseline', ['python3',str(source/'tools/with_resource_delegate.py'),'--native','--','python3',
                str(source/'tests/native/test_session_resources.py'),str(source),str(build/'spatial-authority-launch-probe'),
                str(build/'spatial-security-auth'),str(build/'identityd'),str(build/'spatial-issuer-client'),
                '--baseline-supervisor',str(old),'test_supervisor_binds_sealed_resource_policy'],
                dict(env, SPATIAL_SECURITY_EVIDENCE=str(root/'baseline-native')), expected=1)
            assert 'FAIL: test_supervisor_binds_sealed_resource_policy' in text and 'FAILED (failures=1)' in text
            logs = '\n'.join(path.read_text() for path in (root/'baseline-native').rglob('*-supervisor-*.log'))
            assert 'broker_resources' in logs and 'has no field named' in logs
            record['baseline'] = dict(status='expected_unsupported_supervisor_configuration', binary_sha256=sha(old)); save()
        registered = json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1'], text=True))
        selected = {'broker-resources','process-termination','authority-supervisor','session-resources'}
        counts = []
        for test in registered['tests']:
            if test['name'] not in selected: continue
            original = str(prior/'source') if profile == 'debug' else str(source)
            command = [arg.replace(original,str(source)) for arg in test['command']]
            # Reuse exact Debug binaries. Redirect only equal fixture bytes and evidence paths.
            command = [arg.replace(str(prior/'debug/native'),str(root/profile/'native')) for arg in command]
            test_env = dict(env)
            for prop in test['properties']:
                if prop['name'] != 'ENVIRONMENT': continue
                for value in prop['value']:
                    key, value = value.split('=',1)
                    test_env[key] = value.replace(original,str(source)).replace(str(prior/'debug/native'),str(root/profile/'native'))
            text = run(profile+'-'+test['name'], command, test_env, timeout=250)
            values = [int(x) for x in re.findall(r'Ran (\d+) tests? in ', text)]
            assert len(values)==1 and 'OK' in text and 'skipped=' not in text
            counts.extend(values)
        assert len(counts)==4
        record['profiles'][profile] = dict(passed=sum(counts), suite_counts=counts, failed=0, skipped=0,
            binaries={name:sha(build/name) for name in ('workspaced','spatial-process-scope-probe','spatial-process-termination-probe','spatial-app-init','spatial-authority-supervisor','spatial-session-application-probe','worldd','spatialwm')})
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
        and path.name!='review.json' and path.suffix in ('.log','.json','.py','.textproto')}
    save(); print(host, record['status'], flush=True)
