import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile

root = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
output = root/'local/m02-authority-prepared-launch-01'
source = output/'source'
source.mkdir(parents=True)
with tarfile.open(root/'local/authority-prepared-launch-candidate-01.tar') as archive:
    archive.extractall(source, filter='data')
manifest = json.loads((root/'local/authority-prepared-launch-candidate-01.json').read_text())['files']
def audit():
    for name, sha in manifest.items():
        assert hashlib.sha256((source/name).read_bytes()).hexdigest() == sha, name
    for name, sha in json.loads((source/'acceptance.lock.json').read_text())['files'].items():
        assert hashlib.sha256((source/name).read_bytes()).hexdigest() == sha, name
audit()
for profile, sanitize in [('debug', 'OFF'), ('runtime-sanitize', 'ON')]:
    target = output/profile
    target.mkdir()
    build = target/'build'
    commands = [
        ('configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Debug', '-DSPATIAL_SANITIZERS='+sanitize]),
        ('build', ['cmake', '--build', str(build), '--target', 'spatial-process-scope-probe', '--parallel', '2']),
        ('scope', ['ctest', '--test-dir', str(build), '-V', '-R', '^authority-(process-scope|prepared-launch)$']),
    ]
    results = []
    for name, command in commands:
        with (target/(name+'.log')).open('wb') as log:
            result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT)
        results.append(dict(name=name, command=command, exit_code=result.returncode))
        (target/'run.json').write_text(json.dumps(dict(host=host, results=results), indent=2)+'\n')
        print(host, profile, name, result.returncode, flush=True)
        if result.returncode:
            print((target/(name+'.log')).read_text()[-16000:], flush=True)
            raise SystemExit(result.returncode)
    audit()
print(host, 'prepared launch focused qualification complete', flush=True)
