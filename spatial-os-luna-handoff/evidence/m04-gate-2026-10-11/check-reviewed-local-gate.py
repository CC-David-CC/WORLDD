"""Check downstream admission against real evidence and rejected review variants."""
import copy
import json
from pathlib import Path
from unittest.mock import patch
from reviewed_local_gate import require_reviewed_gate

root = Path('/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff')
require_reviewed_gate(root)
primary_path = root / 'local/m04-full-gate-01/primary-review.json'
primary = json.loads(primary_path.read_text())
original_read = Path.read_text
variants = []
for field, value in (('gate_sha256', 'wrong'), ('status', 'unreviewed'),
                     ('artifact_check_sha256', 'wrong')):
    variant = copy.deepcopy(primary)
    variant[field] = value
    variants.append((field, variant))
variant = copy.deepcopy(primary)
variant['corrections']['storage'] = 'wrong'
variants.append(('correction pin', variant))
for label, variant in variants:
    def read(path, *args, **kwargs):
        if path == primary_path:
            return json.dumps(variant)
        return original_read(path, *args, **kwargs)
    with patch.object(Path, 'read_text', read):
        try:
            require_reviewed_gate(root)
        except AssertionError:
            print('rejected:', label)
        else:
            raise AssertionError('Accepted invalid ' + label)
print('Passed: real reviewed evidence accepted; four invalid variants rejected.')
