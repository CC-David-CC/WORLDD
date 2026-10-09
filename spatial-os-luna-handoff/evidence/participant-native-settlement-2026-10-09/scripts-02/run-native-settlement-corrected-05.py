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
source = root / 'local/m02-participant-fixtures-07/source'
build = source.parent / 'debug/build'
out = root / 'local/native-settlement-corrected-05'
out.mkdir(exist_ok=False)
manifest = json.loads((root / 'local/participant-fixtures-candidate-06.json').read_text())['files']
for name, digest in manifest.items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
server = json.loads((root / 'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest() == server['sha256']
script = root / 'local/test_native_settlement_schedule.py'
command = ['python3', str(script), str(build / 'identityd'), str(build / 'spatial-issuer-client'), str(build / 'worldd'), str(build / 'spatial-security-auth')]
env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, [build / 'python', source / 'tests/core', source])),
           SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build / 'spatial-xprobe'),
           SPATIAL_SCHEDULE_SOURCE=str(source), SPATIAL_CRASH_EVIDENCE=str(out / 'witness'), SPATIAL_SECURITY_EVIDENCE=str(out / 'logs'),
           LIBGL_ALWAYS_SOFTWARE='1')
with (out / 'run.log').open('wb') as log:
    result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
receipt = dict(host=host, command=command, exit_code=result.returncode,
               source_manifest=manifest, server=server,
               test_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
               binaries={name: hashlib.sha256((build/name).read_bytes()).hexdigest()
                         for name in ['identityd','spatial-issuer-client','worldd','workspaced','spatialwm','spatial-xprobe']},
               qualification='native settlement diagnostic; full qualification remains pending')
(out / 'run.json').write_text(json.dumps(receipt, indent=2) + '\n')
with tarfile.open(root / ('local/native-settlement-corrected-05-' + host + '.tar.gz'), 'w:gz') as archive:
    archive.add(out, arcname='baseline-01/' + host)
print((out / 'run.log').read_text(), flush=True)
raise SystemExit(result.returncode)
