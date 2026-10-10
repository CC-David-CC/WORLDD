"""Freeze native replacement coverage against the existing production launch path."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
sha = lambda value: hashlib.sha256(value).hexdigest()
previous = json.loads((root / 'evidence/control-recovery-2026-10-09/stopped-broker-dependency-01/sb16/checks/review.json').read_bytes())
names = list(previous['source']) + ['tests/native/test_session_replacement.py']
files = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in names}
assert len(files) == 343 and len(previous['locked']) == 41
assert all(sha(files[name]) == digest for name, digest in previous['locked'].items())
manifest = dict(files={name: sha(data) for name, data in files.items()}, locked_files=previous['locked'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    purpose='Exercise actual generation replacement, persistent state, surviving application control, retirement barrier and pre-admission revocation with unchanged production launch code.',
    full_gate='Full M02 qualification remains pending on both hosts.')
encoded = (json.dumps(manifest, indent=2) + '\n').encode()
with (root / 'local/session-replacement-source-01.json').open('xb') as stream:
    stream.write(encoded)
archive = root / 'local/session-replacement-source-01.tar'
with tarfile.open(archive, 'x') as tar:
    for name, data in [('manifest.json', encoded)] + [('source/' + name, data) for name, data in files.items()]:
        member = tarfile.TarInfo(name); member.size = len(data); member.mode = 0o644
        tar.addfile(member, io.BytesIO(data))
digest = sha(archive.read_bytes())
unpack = (root / 'local/unpack-stopped-broker-dependency-01.py').read_text()
unpack = unpack.replace('stopped-broker-dependency', 'session-replacement')
unpack = unpack.replace('6dcd70fe7ff16b9bba7801739385b4924798637bf798399615451b0f9a73452f', digest)
with (root / 'local/unpack-session-replacement-01.py').open('xb') as stream:
    stream.write(unpack.encode())
runner = (root / 'local/check-stopped-broker-dependency-01.py').read_text()
runner = runner.replace('m02-stopped-broker-dependency-01', 'm02-session-replacement-01')
start = runner.index('selection = '); end = runner.index('\nrecord = ', start)
runner = runner[:start] + "selection = '^(session-world|session-application|session-dependent-cleanup|session-coordinator-retirement|session-broker-cleanup|session-broker-retirement|session-stopped-broker-dependency)$'" + runner[end:]
start = runner.index('    purpose='); end = runner.index('\n    full_qualification=', start)
runner = runner[:start] + "    purpose='Three native replacement cases exercise the actual production launch path. Seven related native suites check the shared test driver and existing cleanup and retirement behavior.'," + runner[end:]
runner = runner.replace("run('stopped-broker-dependency',", "run('replacement',")
runner = runner.replace("'^session-stopped-broker-dependency$'", "'^session-replacement$'")
with (root / 'local/check-session-replacement-01.py').open('xb') as stream:
    stream.write(runner.encode())
print(json.dumps(dict(source_files=len(files), locked_files=len(previous['locked']), source_archive_sha256=digest)))
