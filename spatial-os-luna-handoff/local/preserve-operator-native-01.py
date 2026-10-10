"""Import byte-identical native operator evidence and verify the current source."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

root = Path(__file__).resolve().parents[1]
archive = root / 'local/operator-native-evidence-01.tar'
sha = lambda value: hashlib.sha256(value).hexdigest()
assert sha(archive.read_bytes()) == '39cf8471a71ec8563fb5cb25ed6b79fb224ce831af1210867b310c6b3ee55b88'
out = root / 'evidence/operator-native-2026-10-10/candidate-01'
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
assert review['status'] == 'native_focused_passed_phase_gate_pending'
assert all(sha((out / 'sb16' / name).read_bytes()) == digest for name, digest in review['attachments'].items())
manifest = json.loads((out / 'sb16/m02-operator-native-01/manifest.json').read_bytes())
assert all(sha((root / name).read_bytes().replace(b'\r\n', b'\n')) == digest for name, digest in manifest['files'].items())
assert len(manifest['files']) == 351 and len(manifest['locked_files']) == 41
assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
print(json.dumps(dict(source_files=351, raw_attachments=len(review['attachments']),
    debug_cases=review['profiles']['debug']['cases'], sanitizer_cases=review['profiles']['runtime-sanitize']['cases'])))
