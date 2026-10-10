import hashlib,json,socket,tarfile
from pathlib import Path,PurePosixPath
assert socket.gethostname().split('.')[0]=='sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
archive=base/'deferred-fence-source-01.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='662f7b510c2e5c8a271eee56984898696c0dac88117408d488beac6908d62a91'
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
