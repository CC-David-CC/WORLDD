"""Freeze the Python-only TLS fixture correction with identical compiled inputs."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
prior = json.loads((root / 'local/session-replacement-source-02.json').read_bytes())
files = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in prior['files']}
assert {name for name in files if sha(files[name]) != prior['files'][name]} == {'tests/native/test_session_replacement.py'}
assert len(files) == 343 and len(prior['locked_files']) == 41
assert all(sha(files[name]) == digest for name, digest in prior['locked_files'].items())
manifest = dict(files={name: sha(data) for name, data in files.items()}, locked_files=prior['locked_files'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    reused_build_candidate='m02-session-replacement-02',
    purpose='Use a fresh authenticated test administrator connection after native recovery. Keep all assertions and production code unchanged.')
encoded = (json.dumps(manifest, indent=2) + '\n').encode()
with (root / 'local/session-replacement-source-03.json').open('xb') as stream: stream.write(encoded)
archive = root / 'local/session-replacement-source-03.tar'
with tarfile.open(archive, 'x') as tar:
    for name, data in [('manifest.json', encoded)] + [('source/' + name, data) for name, data in files.items()]:
        member = tarfile.TarInfo(name); member.size = len(data); member.mode = 0o644
        tar.addfile(member, io.BytesIO(data))
digest = sha(archive.read_bytes())
unpack = (root / 'local/unpack-session-replacement-02.py').read_text()
unpack = unpack.replace('session-replacement-source-02', 'session-replacement-source-03')
unpack = unpack.replace('m02-session-replacement-02', 'm02-session-replacement-03').replace('candidate02', 'candidate03')
unpack = unpack.replace('6bbd9bfe9409e6bbbe88dab71cc3365b9279740c706bd83bb26b0bcdc96cc5bd', digest)
with (root / 'local/unpack-session-replacement-03.py').open('xb') as stream: stream.write(unpack.encode())
print(json.dumps(dict(source_files=343, source_archive_sha256=digest, compiled_input_changes=0)))
