"""Import only regular evidence files from the reviewed host collector."""
import hashlib
import json
import pathlib
import sys
import tarfile

root = pathlib.Path(__file__).resolve().parents[1]
host = sys.argv[1]
assert host in ('sb16', 'sb16-2')
destination = root/'evidence/bootstrap-profiles-2026-10-09'
with tarfile.open(root/'local'/('bootstrap-evidence-'+host+'.tar.gz')) as archive:
    for member in archive.getmembers():
        name = pathlib.PurePosixPath(member.name)
        assert member.isfile() and name.parts[0] == host
        assert not name.is_absolute() and '..' not in name.parts and '\\' not in member.name
        assert name.suffix in ('.json', '.log', '.proto', '.descriptor')
        target = destination.joinpath(*name.parts)
        data = archive.extractfile(member).read()
        if target.exists():
            assert target.read_bytes() == data, 'Existing evidence differs: '+member.name
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
manifest = json.loads((root/'local/bootstrap-tested-tree-02.json').read_text())
for name, expected in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected, name
print(host, 'evidence imported;', len(manifest['files']), 'local source files match')
print((destination/host/'summary.json').read_text())
