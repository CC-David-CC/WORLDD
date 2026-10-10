"""Freeze the native two-host fixture and its production source inputs."""
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

root = Path(__file__).resolve().parents[1]
tag = sys.argv[1]; assert tag in ('01', '02', '03')
previous = json.loads((root / 'local/approval-interruption-source-03.json').read_bytes())
names = list(previous['files']) + ['tests/proto/crosshost_revocation.proto', 'tests/native/crosshost_revocation.py']
data = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in names}
sha = lambda value: hashlib.sha256(value).hexdigest()
assert len(data) == 347
assert all(sha(data[name]) == digest for name, digest in previous['locked_files'].items())
for name, value in data.items():
    if name.endswith('.py'): ast.parse(value, filename=name)
changes = {name for name in names if sha(data[name]) != previous['files'].get(name)}
assert changes == {'CMakeLists.txt', 'tests/native/test_authority_owned_participant.py',
    'tests/native/test_session_application.py', 'tests/proto/crosshost_revocation.proto', 'tests/native/crosshost_revocation.py'}, changes
manifest = dict(files={name: sha(value) for name, value in data.items()}, locked_files=previous['locked_files'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    changed_from_qualified=sorted(changes), purpose='Two real native host sessions share one issuer for cancellation qualification.')
encoded = (json.dumps(manifest, indent=2) + '\n').encode()
prefix = 'crosshost-cancellation-source-' + tag
with (root / 'local' / (prefix + '.json')).open('xb') as stream: stream.write(encoded)
archive = root / 'local' / (prefix + '.tar')
with tarfile.open(archive, 'x') as tar:
    for name, value in [('manifest.json', encoded)] + [('source/' + name, value) for name, value in data.items()]:
        entry = tarfile.TarInfo(name); entry.size = len(value); entry.mode = 0o644
        tar.addfile(entry, io.BytesIO(value))
digest = sha(archive.read_bytes())
unpack = f'''import hashlib,json,socket,tarfile
from pathlib import Path,PurePosixPath
assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
archive=base/'{prefix}.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='{digest}'
root=base/'m02-crosshost-cancellation-{tag}';root.mkdir(exist_ok=False)
with tarfile.open(archive) as tar:
    members=tar.getmembers();assert len(members)==len({{m.name for m in members}})
    for member in members:
        name=PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert root.joinpath(*name.parts).resolve().is_relative_to(root.resolve())
    for member in members:
        path=root.joinpath(*PurePosixPath(member.name).parts);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(tar.extractfile(member).read())
manifest=json.loads((root/'manifest.json').read_bytes())
assert len(manifest['files'])==347 and len(manifest['locked_files'])==41
assert all(hashlib.sha256((root/'source'/n).read_bytes()).hexdigest()==h for n,h in manifest['files'].items())
print('Verified cross-host cancellation source {tag}: 347 files, 41 locked files unchanged')
'''
(root / 'local' / ('unpack-crosshost-cancellation-' + tag + '.py')).write_bytes(unpack.encode())
state_path = root / 'local/driver-state.json'; state = json.loads(state_path.read_bytes())
if 'crosshost_cancellation' in state:
    state.setdefault('crosshost_cancellation_history', []).append(state['crosshost_cancellation'])
state['crosshost_cancellation'] = dict(status='frozen_unrun', candidate='m02-crosshost-cancellation-' + tag,
    source_archive_sha256=digest, source_files=347, locked_files=41, selection='docs/CROSS_HOST_CANCELLATION_CHECKS.txt')
state_path.write_bytes((json.dumps(state, indent=2) + '\n').encode())
print(json.dumps(dict(archive_sha256=digest, source_files=347, changes=sorted(changes))))
