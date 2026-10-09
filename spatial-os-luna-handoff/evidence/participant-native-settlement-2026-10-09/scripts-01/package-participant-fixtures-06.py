import ast
import hashlib
import json
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
source = root / 'local/participant-fixtures-draft-01'
prior = json.loads((root / 'local/participant-fixtures-candidate-05.json').read_text(encoding='utf-8'))['files']
names = sorted(set(prior) | {'tests/core/test_workspace_participant_settlement.py'})
data = {name: (source/name).read_bytes().replace(b'\r\n', b'\n') for name in names}
manifest = {name: hashlib.sha256(value).hexdigest() for name, value in data.items()}
changed = {name for name in manifest if prior.get(name) != manifest[name]}
assert changed == {'CMakeLists.txt', 'src/workspace/main.cpp', 'tests/core/test_workspace_participant_settlement.py'}
for name in names:
    if name.endswith('.py'): ast.parse(data[name], filename=name)
locked = json.loads(data['acceptance.lock.json'])['files']
for name, digest in locked.items(): assert hashlib.sha256(data[name]).hexdigest() == digest, name
for name in prior:
    if name.startswith('tests/') and name.endswith('.py'):
        assert manifest[name] == prior[name], name
for name, value in data.items(): (source/name).write_bytes(value)
(root / 'local/participant-fixtures-candidate-06.json').write_text(json.dumps(dict(
    base='participant-fixtures-candidate-05', files=manifest,
    change='Recheck native cleanup after process reconciliation and before revocation settlement; two stopped-server checks.'),
    indent=2) + '\n', encoding='utf-8', newline='\n')
with tarfile.open(root / 'local/participant-fixtures-candidate-06.tar', 'w') as archive:
    for name in names: archive.add(source/name, arcname=name, recursive=False)
runner = (root / 'local/qualify-participant-fixtures-06.py').read_text(encoding='utf-8')
runner = runner.replace('m02-participant-fixtures-06', 'm02-participant-fixtures-07')
runner = runner.replace('participant-fixtures-candidate-05', 'participant-fixtures-candidate-06')
runner = runner.replace('workspace-participant(-native|-races)?', 'workspace-participant(-native|-races|-settlement)?')
assert 'candidate-05' not in runner and 'm02-participant-fixtures-06' not in runner
(root / 'local/qualify-participant-fixtures-07.py').write_text(runner, encoding='utf-8', newline='\n')
print('Frozen253 files; existing tests and41 locks unchanged. Output07 selects candidate06.')
