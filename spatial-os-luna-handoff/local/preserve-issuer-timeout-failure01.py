"""Import failed qualification evidence and verify the unintegrated candidate."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

root=Path(__file__).resolve().parents[1]
archive=root/'local/issuer-timeout-failure01.tar'
sha=lambda data:hashlib.sha256(data).hexdigest()
assert sha(archive.read_bytes())=='20e46c15d325bdf0a83c0eaae888284180212459747e47fb85099b982727f56e'
out=root/'evidence/control-recovery-2026-10-09/issuer-timeout-diagnostics-failure01'
out.mkdir(parents=True,exist_ok=False)
with tarfile.open(archive) as tar:
    members=tar.getmembers()
    assert len(members)==len({member.name for member in members})
    for member in members:
        name=PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        path=out.joinpath(*name.parts)
        assert path.resolve().is_relative_to(out.resolve())
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(tar.extractfile(member).read())
review=json.loads((out/'primary-review.json').read_bytes())
assert review['status']=='active_revocation_timeout_reproduced'
assert all(sha((out/'sb16'/name).read_bytes())==digest for name,digest in review['attachments'].items())
manifest=json.loads((out/'sb16/m02-issuer-timeout-01/manifest.json').read_bytes())
assert all(sha((root/name).read_bytes().replace(b'\r\n',b'\n'))==digest for name,digest in manifest['files'].items())
assert all(manifest['files'][name]==digest for name,digest in manifest['locked_files'].items())
print(json.dumps(dict(source_files=len(manifest['files']),locked_files_unchanged=len(manifest['locked_files']),
    attachments=len(review['attachments']),status=review['status'])))
