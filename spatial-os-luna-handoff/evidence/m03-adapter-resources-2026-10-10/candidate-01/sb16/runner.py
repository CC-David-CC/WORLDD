"""Build and qualify one frozen adapter-resource increment on an authorized host."""
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
root = base/'m03-adapter-resources-01'; source = root/'source'
lock = (base/'m02-phase-qualification.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
assert not (root/'review.json').exists()
assert shutil.disk_usage(root).free > 4*2**30
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest = json.loads((root/'manifest.json').read_bytes())
assert all(sha(source/name) == digest for name,digest in manifest['files'].items())
assert all(manifest['files'][name] == digest for name,digest in manifest['locked_files'].items())


def busy():
    found = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try: name = (path/'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        if name in ('ninja','ctest','cc1','cc1plus','clang','clang++','bpftrace'): found.append((path.name,name))
    return found


assert not busy(), busy()
record = dict(host=host, status='building', started=time.time(), pid=os.getpid(),
    runner_sha256=sha(Path(__file__)), manifest_sha256=sha(root/'manifest.json'), commands=[], profiles={},
    phase_complete=False, scope='Non-native adapter provisioning and unchanged confinement assertions. Storage and full M03 remain pending.')


def save():
    path = root/'review.json.tmp'; path.write_text(json.dumps(record,indent=2)+'\n'); path.replace(root/'review.json')


def run(label, command, env=None, expected=0, timeout=180):
    assert not busy(), busy()
    item = dict(label=label,argv=command,started=time.time()); record['commands'].append(item); save()
    with (root/(label+'.log')).open('xb') as log:
        result = subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
    item.update(exit_code=result.returncode,ended=time.time(),sha256=sha(root/(label+'.log'))); save()
    text = (root/(label+'.log')).read_text()
    if result.returncode != expected:
        print('\n'.join(text.splitlines()[-100:]),flush=True)
        raise RuntimeError(label+' failed; preserve this attempt')
    return text


save()
try:
    for profile in ('debug','runtime-sanitize'):
        build = root/profile/'build'; env = dict(os.environ)
        for key in ('DISPLAY','SPATIAL_TEST_DISPLAY','LSAN_OPTIONS','SPATIAL_TEST_BROKER_CONFIG','CTEST_PARALLEL_LEVEL'):
            env.pop(key,None)
        env.update(PYTHONPATH=str(build/'python')+':'+str(source), SPATIAL_SCOPE_EVIDENCE=str(root/profile/'witnesses'),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        run(profile+'-configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug',
            '-DBUILD_TESTING=ON','-DSPATIAL_INPUT_TESTS=OFF','-DSPATIAL_SANITIZERS='+('ON' if profile=='runtime-sanitize' else 'OFF')],env,timeout=90)
        run(profile+'-build',['cmake','--build',str(build),'--target','spatial-test-host','spatial-process-scope-probe',
            '--parallel','2'],env,timeout=1200)
        run(profile+'-sync',['sync','-f',str(root)],timeout=60)
        if profile == 'debug':
            old = base/'m02-phase-gate-02/debug/build'
            text = run('baseline',['python3',str(source/'tools/with_resource_delegate.py'),'--native','--','python3',
                str(source/'tools/with_adapter_resources.py'),'--','python3',str(source/'tests/core/test_adapter_resources.py'),
                str(old/'spatial-test-host'),str(old/'workspaced'),str(old/'worldd'),
                'test_explicit_configuration_reaches_kernel_and_closes_private_descriptor'],
                dict(env,SPATIAL_SCOPE_EVIDENCE=str(root/'baseline-witnesses')),expected=1)
            assert 'FAIL: test_explicit_configuration_reaches_kernel_and_closes_private_descriptor' in text
            assert "'manager' != 'broker-manager'" in text and 'FAILED (failures=1)' in text
            record['baseline'] = dict(status='expected_ignored_resource_configuration',
                binaries={name:sha(old/name) for name in ('spatial-test-host','workspaced','worldd')}); save()
        text = run(profile+'-checks',['ctest','--test-dir',str(build),'-V','--output-on-failure','-R',
            '^(adapter-resources|workspace-context|workspace-processes|broker-resources)$'],env,timeout=480)
        counts = [int(n) for n in re.findall(r'Ran (\d+) tests? in ',text)]
        assert len(counts)==4 and '100% tests passed' in text and 'skipped=' not in text
        record['profiles'][profile] = dict(passed=sum(counts),suite_counts=counts,failed=0,skipped=0,
            binaries={name:sha(build/name) for name in ('spatial-test-host','workspaced','worldd','spatial-process-scope-probe','spatial-app-init')})
        save(); print(host,profile,record['profiles'][profile],flush=True)
    assert all(sha(source/name) == digest for name,digest in manifest['files'].items())
    logs = [p for p in root.rglob('*.log') if 'source' not in p.relative_to(root).parts and 'build' not in p.relative_to(root).parts]
    for path in logs:
        assert not re.search(r'ERROR: AddressSanitizer|ERROR: LeakSanitizer|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:',path.read_text(errors='replace')),path
    record.update(status='passed_review_pending',sanitizer_logs_scanned=len(logs),sanitizer_matches=0)
except BaseException as error:
    record.update(status='failed',error=type(error).__name__+': '+str(error));raise
finally:
    record['ended']=time.time()
    record['attachments']={p.relative_to(root).as_posix():sha(p) for p in root.rglob('*')
        if p.is_file() and 'source' not in p.relative_to(root).parts and 'build' not in p.relative_to(root).parts
        and p.name!='review.json' and p.suffix in ('.json','.log','.py','.textproto')}
    save();print(host,record['status'],flush=True)
