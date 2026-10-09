"""Preserve the original preceding suite order with diagnostic-only binaries."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
prior = r/'local/world-bootstrap-diagnostic-run-01'
source, build = prior/'source', prior/'build'
manifest = json.loads((r/'local/world-bootstrap-diagnostic-01.json').read_text())['files']
def audit():
    for name, digest in manifest.items():
        assert hashlib.sha256((source/name).read_bytes()).hexdigest() == digest, name
audit()
completed = json.loads((r/'local/world-bootstrap-suite-diagnostic-02/repetitions.json').read_text())
assert len(completed) == 8 and all(v['exit_code'] == 0 for v in completed)
assert (r/('local/world-bootstrap-suite-diagnostic-'+host+'-02.tar.gz')).is_file()
out = r/'local/world-bootstrap-prefix-diagnostic-03'
out.mkdir()
original = r/'local/m02-supervisor-world-handoff-02/runtime-sanitize/core.log'
names = re.findall(r'^\s*Start\s+\d+:\s+(\S+)', original.read_text(), re.M)
assert len(names) == 82 and len(set(names)) == 82
names = names[:names.index('world-bootstrap')+1]
assert len(names) == 30
pattern = '^('+'|'.join(re.escape(n) for n in names)+')$'
listed = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1', '-R', pattern]))
assert [t['name'] for t in listed['tests']] == names
server = json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest() == server['sha256']
env = dict(os.environ, SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build/'spatial-xprobe'),
    SPATIAL_SECURITY_EVIDENCE=str(out/'service-logs'), PYTHONPATH=str(build/'python')+':'+str(source),
    SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]), SPATIAL_EVIDENCE_DIR=str(out/'cases'),
    ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
command = ['ctest', '--test-dir', str(build), '-V', '-R', pattern]
record = dict(host=host, diagnostic_only=True, qualification=False, suites=names, command=command,
              source_files=manifest, server=server)
(out/'start.json').write_text(json.dumps(record, indent=2)+'\n')
for label in ('before',):
    (out/(label+'-resources.txt')).write_text(Path('/proc/meminfo').read_text()+Path('/proc/loadavg').read_text())
print(host, 'starting original 30-suite prefix with unchanged assertions and deadlines', flush=True)
with (out/'core.log').open('xb') as log:
    result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
record['exit_code'] = result.returncode
(out/'after-resources.txt').write_text(Path('/proc/meminfo').read_text()+Path('/proc/loadavg').read_text())
audit()
(out/'result.json').write_text(json.dumps(record, indent=2)+'\n')
if result.returncode:
    print((out/'core.log').read_text()[-12000:], flush=True)
    for p in sorted((out/'service-logs').glob('*.log')):
        value = p.read_text(errors='replace')
        if 'participant diagnostic:' in value or 'identity service disabled' in value:
            print(p.name, value[-5000:], flush=True)
with tarfile.open(r/('local/world-bootstrap-prefix-diagnostic-'+host+'-03.tar.gz'), 'w:gz') as archive:
    # Runtime databases, credentials, and generated environments stay on the host.
    for p in sorted(out.rglob('*')):
        relative = p.relative_to(out)
        if p.is_file() and 'cases' not in relative.parts and p.suffix in ('.log', '.json', '.txt'):
            archive.add(p, arcname='prefix-diagnostic-03/'+host+'/'+relative.as_posix())
print(host, 'prefix diagnostic complete', result.returncode, '; no qualification claim', flush=True)
