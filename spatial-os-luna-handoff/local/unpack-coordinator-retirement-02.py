"""Install one immutable candidate after archive and path verification."""
import hashlib
from pathlib import Path, PurePosixPath
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
root = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
archive = root / 'coordinator-retirement-source-02.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == 'bd293dcb7a8598addbbda2a261a89961a03e7a65e299c0355c873e7d6efce178'
target = root / 'm02-coordinator-retirement-02'
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
print('Verified and unpacked coordinator-retirement candidate02')
