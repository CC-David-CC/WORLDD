"""Link an independent lifecycle fault peer against unchanged qualified production libraries."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
prior = r / 'local/m02-coordinator-retirement-02'
review = json.loads((prior / 'checks/review.json').read_bytes()); assert review['status'] == 'passed'
source = prior / 'source'; build = prior / 'debug/build'
out = r / 'local/m02-session-launch-exit-baseline-01'; out.mkdir(exist_ok=False)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for path in Path('/proc').iterdir():
    if not path.name.isdigit(): continue
    try: name = (path / 'comm').read_text().strip()
    except (PermissionError, FileNotFoundError, ProcessLookupError): continue
    assert name not in ('ctest', 'cc1', 'cc1plus', 'clang', 'ninja'), (path, name)
assert all(sha(source / n) == h for n, h in review['source'].items())
libraries = {p.name: sha(p) for p in build.glob('*.a')}
cpp = r / 'local/session-launch-exit-baseline-01.cpp'; test = r / 'local/test-session-launch-exit-baseline-01.py'
for original, name in ((cpp, 'input.cpp'), (test, 'input.py')):
    with (out / name).open('xb') as stream: stream.write(original.read_bytes())
link = subprocess.check_output(['ninja', '-C', str(build), '-t', 'commands', 'spatial-session-broker-probe'], text=True).splitlines()[-1]
args = shlex.split(link); assert args[:3] == [':', '&&', '/usr/bin/c++'] and args[-2:] == ['&&', ':']
tail = args[args.index('-o') + 2:-2]
assert all(arg.endswith(('.a', '.so')) for arg in tail), tail
binary = out / 'session-launch-exit-probe'
compile_command = ['/usr/bin/c++', '-std=c++20', '-g', '-I' + str(source / 'src'), '-I' + str(build),
    str(out / 'input.cpp'), '-o', str(binary), *tail]
record = dict(status='building', production_source=review['source'], production_libraries=libraries,
    compile_command=compile_command, started=time.time())
save = lambda: (out / 'review.json').write_text(json.dumps(record, indent=2) + '\n')
save()
with (out / 'build.log').open('xb') as stream:
    result = subprocess.run(compile_command, cwd=build, stdout=stream, stderr=subprocess.STDOUT)
record['compile_exit_code'] = result.returncode; save()
assert result.returncode == 0, (out / 'build.log').read_text()
record['binary_sha256'] = sha(binary)
command = ['python3', str(out / 'input.py'), str(binary)]
record['command'] = command; save()
env = dict(os.environ, PYTHONPATH=str(build / 'python') + ':' + str(source))
with (out / 'baseline.log').open('xb') as stream:
    result = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
text = (out / 'baseline.log').read_text()
record.update(status='unexpected_result', exit_code=result.returncode, ended=time.time(), log_sha256=sha(out / 'baseline.log')); save()
assert result.returncode == 1 and 'Ran 1 test' in text and 'FAILED (failures=1)' in text, text
assert 'Retry forgot the rejected supervisor exit status and accepted cleanup' in text, text
assert all(sha(build / n) == h for n, h in libraries.items())
record['status'] = 'rejected_exit_status_forgotten_on_retry'; save()
print(json.dumps(dict(status=record['status'], log=str(out / 'baseline.log'))))
