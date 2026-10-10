"""Create a separate candidate from the exact integrated 294-file source."""
import hashlib
import json
from pathlib import Path

r = Path(__file__).resolve().parents[1]
manifest = json.loads((r/'local/supervisor-crash-fencing-candidate-01.json').read_text())['files']
draft = r/'local/control-owner-draft-01'
assert len(manifest) == 294 and not draft.exists()
for name, digest in manifest.items():
    data = (r/name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest, name
    target = draft/name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
print(f'Prepared {len(manifest)} verified files in {draft}')
