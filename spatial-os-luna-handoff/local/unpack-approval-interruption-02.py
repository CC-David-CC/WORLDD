"""Install one immutable candidate after archive and path verification."""
import hashlib
from pathlib import Path, PurePosixPath
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
root = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
archive = root / 'approval-interruption-source-02.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == 'f573ffeb3d7de193057a78f0ef1814e54e95cea56a1d9d19914fee4a1fccdb1c'
target = root / 'm02-approval-interruption-02'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({member.name for member in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name == 'manifest.json' or member.name.startswith('source/')
    target.mkdir()
    for member in members:
        path = target.joinpath(*PurePosixPath(member.name).parts)
        assert path.resolve().is_relative_to(target.resolve())
        path.parent.mkdir(exist_ok=True, parents=True)
        with path.open('xb') as output: output.write(tar.extractfile(member).read())
print('Verified and unpacked session-replacement candidate03')
