import hashlib,json,socket,tarfile
from pathlib import Path,PurePosixPath
assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
archive=base/'crosshost-cancellation-source-02.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='e6244d03e7cb1bede5e321836f52166bf5873255c5a1035648d6d0c49fb6d84f'
root=base/'m02-crosshost-cancellation-02';root.mkdir(exist_ok=False)
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
assert len(manifest['files'])==347 and len(manifest['locked_files'])==41
assert all(hashlib.sha256((root/'source'/n).read_bytes()).hexdigest()==h for n,h in manifest['files'].items())
print('Verified cross-host cancellation source 02: 347 files, 41 locked files unchanged')
