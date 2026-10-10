"""Verify and retain the immutable durability baseline archive on the controller."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

root = Path(__file__).resolve().parents[1]
archive = root / 'local/dependent-cleanup-baseline-01-sb16.tar.gz'
sha = lambda data: hashlib.sha256(data).hexdigest()
assert sha(archive.read_bytes()) == '00035ae90e5a0968c564afe8e34fff9f1f19d801cb09f0709f92a3c1782d71d8'
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'dependent-cleanup-baseline01/sb16'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers(); assert len(members) == len({m.name for m in members})
    for member in members:
        path = PurePosixPath(member.name)
        assert member.isfile() and not path.is_absolute() and '..' not in path.parts
        assert member.name.startswith('dependent-cleanup-baseline01/sb16/')
        destination = evidence.joinpath(*path.parts)
        assert destination.resolve().is_relative_to(evidence.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream: stream.write(tar.extractfile(member).read())
review = json.loads((target / 'baseline-review.json').read_bytes())
assert all(sha((target / name).read_bytes()) == digest for name, digest in review['attachments'].items())
print(json.dumps(dict(status=review['status'], attachments=len(review['attachments']))))
