"""Freeze reviewed stopped-broker dependency source and its selected Linux checks."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
previous = json.loads((root / 'evidence/control-recovery-2026-10-09/broker-retirement-01/sb16/checks/review.json').read_bytes())
previous['source']['tests/native/test_stopped_broker_dependency.py'] = ''
files = {n: (root / n).read_bytes().replace(b'\r\n', b'\n') for n in previous['source']}
assert len(files) == 342 and len(previous['locked']) == 41
assert all(sha(files[n]) == h for n, h in previous['locked'].items())
manifest = dict(files={n: sha(v) for n, v in files.items()}, locked_files=previous['locked'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    purpose='Persist exact checked stopped-broker scope cleanup as version 2 world dependency evidence. Preserve version 1 receipts, immutable retries, local durability, exact ownership and separate world retirement.',
    full_gate='M02 qualification remains pending on both hosts.')
data = (json.dumps(manifest, indent=2) + '\n').encode()
with (root / 'local/stopped-broker-dependency-source-01.json').open('xb') as stream: stream.write(data)
archive = root / 'local/stopped-broker-dependency-source-01.tar'
with tarfile.open(archive, 'x') as tar:
    for name, content in [('manifest.json', data)] + [('source/' + n, v) for n, v in files.items()]:
        item = tarfile.TarInfo(name); item.size = len(content); item.mode = 0o644
        tar.addfile(item, io.BytesIO(content))
digest = sha(archive.read_bytes())
unpack = (root / 'local/unpack-session-broker-cleanup-01.py').read_text()
unpack = unpack.replace('session-broker-cleanup-source-01', 'stopped-broker-dependency-source-01')
unpack = unpack.replace('m02-session-broker-cleanup-01', 'm02-stopped-broker-dependency-01')
unpack = unpack.replace('d01335272f559612802a947d2d9dd4dea9ea747cbfc8b554fd6c36acb940f191', digest)
unpack = unpack.replace('session-broker-cleanup candidate01', 'stopped-broker-dependency candidate01')
with (root / 'local/unpack-stopped-broker-dependency-01.py').open('xb') as stream: stream.write(unpack.encode())
runner = (root / 'local/check-session-broker-cleanup-01.py').read_text()
runner = runner.replace('m02-session-broker-cleanup-01', 'm02-stopped-broker-dependency-01')
start = runner.index('selection = '); end = runner.index('\nrecord = ', start)
runner = runner[:start] + "selection = '^(session-dependent-cleanup|session-coordinator-retirement|session-broker-retirement|session-broker-cleanup|session-world|session-application|authority-launch-coordinator|authority-prepared-launch)$'" + runner[end:]
start = runner.index('    purpose='); end = runner.index('\n    full_qualification=', start)
runner = runner[:start] + "    purpose='Nine real cleanup scenarios cover both dependency versions, actual stopped world and broker ownership, local and issuer failures, immutable bytes, absent receipts, and denied reconstruction. Eight related suites cover shared journal, coordinator retirement, broker retirement and existing native application recovery.'," + runner[end:]
runner = runner.replace("run('broker-cleanup',", "run('stopped-broker-dependency',")
runner = runner.replace("'^(supervisor-session-exit|session-broker-cleanup)$'", "'^session-stopped-broker-dependency$'")
with (root / 'local/check-stopped-broker-dependency-01.py').open('xb') as stream: stream.write(runner.encode())
print(json.dumps(dict(source_files=len(files), locked_files=len(previous['locked']), source_archive_sha256=digest)))
