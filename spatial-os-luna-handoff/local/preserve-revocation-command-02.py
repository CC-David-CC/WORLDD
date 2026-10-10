"""Import byte-identical selected evidence and check the reviewed working source."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

root = Path(__file__).resolve().parents[1]
archive = root / 'local/revocation-command-evidence-02.tar'
sha = lambda data: hashlib.sha256(data).hexdigest()
assert sha(archive.read_bytes()) == '9d6ede10b3c76340c4f12c96ab836990dec9114217f342e0b81e439de6a6dd21'
out = root / 'evidence/revocation-command-2026-10-10/candidate-02'
out.mkdir(parents=True, exist_ok=False)
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({member.name for member in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        path = out.joinpath(*name.parts)
        assert path.resolve().is_relative_to(out.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
review = json.loads((out / 'primary-review.json').read_bytes())
assert review['status'] == 'focused_passed_phase_gate_pending'
assert all(sha((out / 'sb16' / name).read_bytes()) == digest for name, digest in review['attachments'].items())
manifest = json.loads((out / 'sb16/m02-revocation-command-02/manifest.json').read_bytes())
assert all(sha((root / name).read_bytes().replace(b'\r\n', b'\n')) == digest for name, digest in manifest['files'].items())
assert len(manifest['files']) == 350 and len(manifest['locked_files']) == 41
assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
print(json.dumps(dict(source_files=350, locked_files_unchanged=41, raw_attachments=len(review['attachments']),
    debug=review['profiles']['debug']['cases'], runtime_sanitizers=review['profiles']['runtime-sanitize']['cases'])))
