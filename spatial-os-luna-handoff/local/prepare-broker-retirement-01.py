"""Freeze reviewed broker retirement source and its selected Linux checks."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
previous = json.loads((root / 'evidence/control-recovery-2026-10-09/session-broker-cleanup-01/sb16/checks/review.json').read_bytes())
previous['source']['tests/native/test_broker_retirement.py'] = ''
files = {n: (root / n).read_bytes().replace(b'\r\n', b'\n') for n in previous['source']}
assert len(files) == 341 and len(previous['locked']) == 41
assert all(sha(files[n]) == h for n, h in previous['locked'].items())
manifest = dict(files={n: sha(v) for n, v in files.items()}, locked_files=previous['locked'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    purpose='Retire only the broker generation after actual retained process/native cleanup. Bind the exact saved launch bytes, preserve issuer and local durability, reject unowned cleanup and tampering, and retain exact retries.',
    full_gate='M02 qualification remains pending on both hosts.')
data = (json.dumps(manifest, indent=2) + '\n').encode()
with (root / 'local/broker-retirement-source-01.json').open('xb') as stream: stream.write(data)
archive = root / 'local/broker-retirement-source-01.tar'
with tarfile.open(archive, 'x') as tar:
    for name, content in [('manifest.json', data)] + [('source/' + n, v) for n, v in files.items()]:
        item = tarfile.TarInfo(name); item.size = len(content); item.mode = 0o644
        tar.addfile(item, io.BytesIO(content))
digest = sha(archive.read_bytes())
unpack = (root / 'local/unpack-session-broker-cleanup-01.py').read_text()
unpack = unpack.replace('session-broker-cleanup-source-01', 'broker-retirement-source-01')
unpack = unpack.replace('m02-session-broker-cleanup-01', 'm02-broker-retirement-01')
unpack = unpack.replace('d01335272f559612802a947d2d9dd4dea9ea747cbfc8b554fd6c36acb940f191', digest)
unpack = unpack.replace('session-broker-cleanup candidate01', 'broker-retirement candidate01')
with (root / 'local/unpack-broker-retirement-01.py').open('xb') as stream: stream.write(unpack.encode())
runner = (root / 'local/check-session-broker-cleanup-01.py').read_text()
runner = runner.replace('m02-session-broker-cleanup-01', 'm02-broker-retirement-01')
start = runner.index('selection = '); end = runner.index('\nrecord = ', start)
runner = runner[:start] + "selection = '^(session-broker|session-broker-cleanup|session-coordinator-retirement|session-dependent-cleanup|identity-authority-retirement|identity-authority-retirement-client|authority-prepared-launch|authority-launch-coordinator|authority-supervisor)$'" + runner[end:]
start = runner.index('    purpose='); end = runner.index('\n    full_qualification=', start)
runner = runner[:start] + "    purpose='Nine actual broker retirement scenarios cover retained cleanup, exact digests and receipts, independent generations, tampering, failed writes, exact retries and absent ownership. Related suites cover shared journal, issuer exchange, supervisor and session boundaries.'," + runner[end:]
runner = runner.replace("run('broker-cleanup',", "run('broker-retirement',")
runner = runner.replace("'^(supervisor-session-exit|session-broker-cleanup)$'", "'^session-broker-retirement$'")
with (root / 'local/check-broker-retirement-01.py').open('xb') as stream: stream.write(runner.encode())
print(json.dumps(dict(source_files=len(files), locked_files=len(previous['locked']), source_archive_sha256=digest)))
