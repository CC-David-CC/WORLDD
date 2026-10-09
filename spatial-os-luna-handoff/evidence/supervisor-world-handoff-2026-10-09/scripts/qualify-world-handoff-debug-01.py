"""Run independent frozen world handoff checks beside the existing broker sanitizer jobs."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
baseline = json.loads((r / 'local/supervisor-world-handoff-debug-baseline-01/summary.json').read_text())
assert baseline['host'] == host and baseline['profiles']['debug']['expected_missing_handoff_failures'] == 1
qualified = json.loads((r / ('local/supervisor-broker-full-summary-debug-' + host + '-02.json')).read_text())['debug']
assert tuple(qualified[k] for k in ('core_checks', 'core_suites', 'harness_checks', 'P10_cases', 'failed', 'skipped')) == (1393, 93, 51, 151, 0, 0)
out = r / 'local/m02-supervisor-world-handoff-01'
source = out / 'source'; source.mkdir(parents=True)
with tarfile.open(r / 'local/supervisor-world-handoff-candidate-01.tar') as archive:
    archive.extractall(source, filter='data')
manifest = json.loads((r / 'local/supervisor-world-handoff-candidate-01.json').read_text())['files']
assert len(manifest) == 290
def audit():
    for files in (manifest, json.loads((source / 'acceptance.lock.json').read_text())['files']):
        for name, digest in files.items():
            assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
audit()
server = json.loads((r / 'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest() == server['sha256']
target = out / 'debug'; target.mkdir()
build = target / 'build'
env = dict(os.environ, SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build/'spatial-xprobe'),
    SPATIAL_SECURITY_EVIDENCE=str(target/'server'), PYTHONPATH=str(build/'python')+':'+str(source),
    ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
commands = [
    ('configure', ['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS=OFF']),
    ('build', ['cmake','--build',str(build),'--parallel','2']),
    ('harness', ['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
    ('handoff', ['ctest','--test-dir',str(build),'-V','-R','^authority-world-handoff$']),
    ('regressions', ['ctest','--test-dir',str(build),'-V','-R','^authority-world-handoff-regressions$'])]
results = []
for name, command in commands:
    with (target/(name+'.log')).open('xb') as log:
        result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=result.returncode))
    (target/'run.json').write_text(json.dumps(dict(host=host,source_files=290,results=results,server=server),indent=2)+'\n')
    print(host,'debug',name,result.returncode,flush=True)
    if result.returncode:
        print((target/(name+'.log')).read_text()[-20000:],flush=True)
        raise SystemExit(result.returncode)
audit()
print(host,'world handoff Debug checks complete',flush=True)
