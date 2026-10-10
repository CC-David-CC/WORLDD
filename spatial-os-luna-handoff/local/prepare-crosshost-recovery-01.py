"""Freeze the recovery fixture without changing compiled production inputs."""
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

root = Path(__file__).resolve().parents[1]
tag = sys.argv[1] if len(sys.argv) == 2 else '01'
assert tag in ('01', '02')
previous = json.loads((root / 'local/crosshost-cancellation-source-03.json').read_bytes())
data = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in previous['files']}
sha = lambda value: hashlib.sha256(value).hexdigest()
assert len(data) == 347
assert all(sha(data[name]) == digest for name, digest in previous['locked_files'].items())
for name, value in data.items():
    if name.endswith('.py'): ast.parse(value, filename=name)
changes = {name for name in data if sha(data[name]) != previous['files'][name]}
assert changes == {'tests/native/crosshost_revocation.py', 'tests/proto/crosshost_revocation.proto'}
manifest = dict(files={name: sha(value) for name, value in data.items()}, locked_files=previous['locked_files'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    changed_from_qualified=sorted(changes), purpose='Native cross-host crash, dependent cleanup, and exact issuer retirement.')
encoded = (json.dumps(manifest, indent=2) + '\n').encode()
prefix = 'crosshost-recovery-source-' + tag
with (root / 'local' / (prefix + '.json')).open('xb') as stream: stream.write(encoded)
archive = root / 'local' / (prefix + '.tar')
with tarfile.open(archive, 'x') as tar:
    for name, value in [('manifest.json', encoded)] + [('source/' + name, value) for name, value in data.items()]:
        entry = tarfile.TarInfo(name); entry.size = len(value); entry.mode = 0o644
        tar.addfile(entry, io.BytesIO(value))
digest = sha(archive.read_bytes())
unpack = '''import hashlib,json,socket,subprocess,tarfile
from pathlib import Path,PurePosixPath
assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
archive=base/'crosshost-recovery-source-01.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==ARCHIVE_HASH
root=base/'m02-crosshost-recovery-01';root.mkdir(exist_ok=False)
with tarfile.open(archive) as tar:
    members=tar.getmembers();assert len(members)==len({m.name for m in members})
    for member in members:
        name=PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert root.joinpath(*name.parts).resolve().is_relative_to(root.resolve())
    for member in members:
        path=root.joinpath(*PurePosixPath(member.name).parts);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(tar.extractfile(member).read())
manifest=json.loads((root/'manifest.json').read_bytes());source=root/'source'
assert len(manifest['files'])==347 and len(manifest['locked_files'])==41
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==h for n,h in manifest['files'].items())
bindings=root/'python';bindings.mkdir()
command=['protoc','--python_out='+str(bindings)]
command += ['--proto_path='+str(source/n) for n in ('tests/proto','proto/spatial/workspace/v1',
    'proto/spatial/supervisor/v1','proto/spatial/security/v1','proto/spatial/identity/v1',
    'proto/spatial/authority/v1','proto/spatial/core/v1')]
command += [str(source/'tests/proto/crosshost_revocation.proto')]
result=subprocess.run(command,capture_output=True)
(root/'bindings.log').write_bytes(result.stdout+result.stderr)
assert result.returncode==0,result.stderr
review=dict(command=command,exit_code=result.returncode,
    schema_sha256=manifest['files']['tests/proto/crosshost_revocation.proto'],
    binding_sha256=hashlib.sha256((bindings/'crosshost_revocation_pb2.py').read_bytes()).hexdigest())
(root/'bindings-review.json').write_text(json.dumps(review,indent=2)+'\\n')
print('Verified 347 recovery source files and generated the test-only protobuf binding')
'''.replace('ARCHIVE_HASH', repr(digest)).replace('recovery-01', 'recovery-' + tag).replace('recovery-source-01', 'recovery-source-' + tag)
(root / ('local/unpack-crosshost-recovery-' + tag + '.py')).write_bytes(unpack.encode())
state_path = root / 'local/driver-state.json'; state = json.loads(state_path.read_bytes())
if 'crosshost_recovery' in state:
    state.setdefault('crosshost_recovery_history', []).append(state['crosshost_recovery'])
state['crosshost_recovery'] = dict(status='frozen_unrun', candidate='m02-crosshost-recovery-' + tag,
    source_archive_sha256=digest, source_files=347, locked_files=41, selection='docs/CROSS_HOST_RECOVERY_CHECKS.txt',
    reused_binaries='m02-crosshost-cancellation-01', compiled_input_changes=0)
state_path.write_bytes((json.dumps(state, indent=2) + '\n').encode())
print(json.dumps(dict(archive_sha256=digest, changed=sorted(changes))))
