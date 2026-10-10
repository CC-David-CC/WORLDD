"""Freeze the deferred fence increment and its five native regressions."""
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
previous = json.loads((root / 'evidence/control-recovery-2026-10-09/crosshost-recovery-02/sb16/m02-crosshost-recovery-02/manifest.json').read_bytes())
names = list(previous['files']) + ['tests/native/test_deferred_fence.py']
data = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in names}
sha = lambda value: hashlib.sha256(value).hexdigest()
assert all(sha(data[name]) == digest for name, digest in previous['locked_files'].items())
for name, value in data.items():
    if name.endswith('.py'): ast.parse(value, filename=name)
changed = {name for name in data if sha(data[name]) != previous['files'].get(name)}
assert changed == {'CMakeLists.txt', 'tests/native/test_deferred_fence.py', 'src/supervisor/launch.cpp',
    'src/supervisor/launch.hpp', 'src/supervisor/session_launch.cpp', 'src/supervisor/session_launch.hpp',
    'src/supervisor/session_broker.cpp', 'src/supervisor/session_coordinator.cpp'}
manifest = dict(files={name: sha(value) for name, value in data.items()}, locked_files=previous['locked_files'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    changed_from_qualified=sorted(changed))
encoded = (json.dumps(manifest, indent=2) + '\n').encode()
with (root / 'local/deferred-fence-source-01.json').open('xb') as stream: stream.write(encoded)
archive = root / 'local/deferred-fence-source-01.tar'
with tarfile.open(archive, 'x') as tar:
    for name, value in [('manifest.json', encoded)] + [('source/' + name, value) for name, value in data.items()]:
        entry = tarfile.TarInfo(name); entry.size = len(value); entry.mode = 0o644
        tar.addfile(entry, io.BytesIO(value))
unpack = '''import hashlib,json,socket,tarfile
from pathlib import Path,PurePosixPath
assert socket.gethostname().split('.')[0]=='sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
archive=base/'deferred-fence-source-01.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==ARCHIVE_HASH
root=base/'m02-deferred-fence-01';root.mkdir(exist_ok=False)
with tarfile.open(archive) as tar:
    members=tar.getmembers();assert len(members)==len({m.name for m in members})
    for member in members:
        name=PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert root.joinpath(*name.parts).resolve().is_relative_to(root.resolve())
    for member in members:
        path=root.joinpath(*PurePosixPath(member.name).parts);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(tar.extractfile(member).read())
manifest=json.loads((root/'manifest.json').read_bytes())
assert len(manifest['files'])==348 and len(manifest['locked_files'])==41
assert all(hashlib.sha256((root/'source'/name).read_bytes()).hexdigest()==digest for name,digest in manifest['files'].items())
print('Verified 348 source files and 41 unchanged locked files')
'''.replace('ARCHIVE_HASH', repr(sha(archive.read_bytes())))
(root / 'local/unpack-deferred-fence-01.py').write_bytes(unpack.encode())
build = (root / 'local/build-crosshost-cancellation-01.py').read_text().replace('m02-crosshost-cancellation-01', 'm02-deferred-fence-01')
build = build.replace("'world-session-python-protobuf']]", "'world-session-python-protobuf', 'spatial-session-broker-probe', 'spatial-session-world-probe']]")
(root / 'local/build-deferred-fence-01.py').write_bytes(build.encode())
print(json.dumps(dict(files=len(data), archive_sha256=sha(archive.read_bytes()), changed=sorted(changed))))
