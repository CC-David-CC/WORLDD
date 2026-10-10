"""Build an immutable owner-channel candidate, then run focused real-broker checks."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
root = r/'local/m02-control-owner-01'
source = root/'source'
manifest = json.loads((root/'manifest.json').read_text())
out = root/'qualification'
out.mkdir(exist_ok=False)
record = dict(host=host, source_files=len(manifest['files']), status='running', profiles={}, started=time.time())

def save():
    (out/'summary.json').write_text(json.dumps(record, indent=2)+'\n')

def audit():
    for group in ('files', 'locked_files'):
        for name, digest in manifest[group].items():
            assert hashlib.sha256((source/name).read_bytes()).hexdigest() == digest, name

def run(name, command, target, env):
    audit()
    start = time.time()
    with (target/(name+'.log')).open('xb') as log:
        result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
    entry = dict(name=name, command=command, start=start, end=time.time(), exit_code=result.returncode)
    print(host, name, result.returncode, flush=True)
    if result.returncode:
        print((target/(name+'.log')).read_text()[-7000:], flush=True)
    return entry

try:
    audit()
    server = json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
    assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest() == server['sha256']
    record['server'] = server
    for profile in ('debug', 'runtime-sanitize'):
        target = out/profile
        target.mkdir()
        build = root/profile/'build'
        env = dict(os.environ, SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build/'spatial-xprobe'),
                   SPATIAL_SECURITY_EVIDENCE=str(target/'native'),
                   PYTHONPATH=str(build/'python')+':'+str(source),
                   ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        results = []
        record['profiles'][profile] = dict(results=results, build=str(build))
        save()
        commands = [
            ('configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
                           '-DCMAKE_BUILD_TYPE=Debug', '-DSPATIAL_INPUT_TESTS=ON',
                           '-DSPATIAL_SANITIZERS='+('ON' if profile == 'runtime-sanitize' else 'OFF'),
                           '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache']),
            ('build', ['cmake', '--build', str(build), '--parallel', '2']),
            ('harness', ['python3', '-m', 'unittest', 'discover', '-s', 'tests/harness', '-t', '.', '-q']),
            ('focused', ['ctest', '--test-dir', str(build), '-V', '-R',
                         '^(control-owner-channels|application-control-lease|application-access|broker-control-client-live|world-application-control)$'])]
        for name, command in commands:
            results.append(run(name, command, target, env))
            save()
            if results[-1]['exit_code']:
                raise RuntimeError(profile+' '+name+' failed')
        record['profiles'][profile]['binaries'] = {name:hashlib.sha256((build/name).read_bytes()).hexdigest()
            for name in ('workspaced','worldd','identityd','spatialwm','spatial-issuer-client','spatial-security-auth')}
        save()
    record['status'] = 'focused_passed'
except BaseException:
    record['status'] = 'failed'
    raise
finally:
    record['ended'] = time.time()
    save()
