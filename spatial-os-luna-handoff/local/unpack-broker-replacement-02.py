"""Install one immutable candidate after archive and path verification."""
import hashlib
from pathlib import Path, PurePosixPath
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
root = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
archive = root / 'broker-replacement-source-02.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == '3373d45a8e08609b8e4f75f2fbd8820b17878fd74627de359cfaa6b0f3b40f5b'
target = root / 'm02-broker-replacement-02'
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
