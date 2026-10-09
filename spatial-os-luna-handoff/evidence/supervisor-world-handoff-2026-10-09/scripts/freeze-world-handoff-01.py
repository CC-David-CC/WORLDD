import ast
import hashlib
import json
from pathlib import Path
import tarfile

r = Path(__file__).resolve().parents[1]
source = r / 'local/supervisor-world-handoff-draft-01'
base = json.loads((r / 'local/supervisor-broker-handoff-candidate-02.json').read_text())['files']
files = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in source.rglob('*') if p.is_file()}
assert len(files) == 290
locked = json.loads((source / 'acceptance.lock.json').read_text())['files']
for names in (locked, {n: h for n, h in base.items() if n.startswith(('tests/', 'harness/'))}):
    for name, digest in names.items():
        assert files[name] == digest, name
for name in ('test_supervisor_world_handoff.py', 'test_supervisor_world_handoff_regressions.py'):
    tree = ast.parse((source / 'tests/native' / name).read_text())
    count = sum(isinstance(n, ast.FunctionDef) and n.name.startswith('test_')
                for c in tree.body if isinstance(c, ast.ClassDef) for n in c.body)
    assert count == (1 if name == 'test_supervisor_world_handoff.py' else 11)
record = dict(candidate=1, source_files=290, base_source_files=283,
    files=files, changed=sorted(n for n in files if base.get(n) != files[n]),
    status='frozen_unbuilt_unqualified_not_integrated', primary_review=[
        'Both-host unchanged Debug baseline failure preserved before drafting correction.',
        'Additive protobuf field12, new compositor schema, existing schemas/tests/locks retained.',
        'Supervisor derives only its own issued COMPOSITOR cookie and verified server pins.',
        'World/compositor consume a fully sealed bounded descriptor and disable process inspection.',
        'World forwards the credential descriptor only; child closes other nonstandard descriptors.',
        'XCB adapter verifies SO_PEERCRED on the exact connected socket before sending authentication.',
        'Xlib receives only a non-secret placeholder; the adapter uses xcb_connect_to_fd after peer verification.',
        'Actual compositor connection checks native role and server ID before ordinary rendering.',
        'Existing display fixtures forward to the normal libxcb entry point.',
        'Positive-control native startup precedes direct denials; fake peer must receive zero authentication bytes.'
    ], remaining=['Build and run baseline plus11 correction methods on both hosts.',
        'Preserve and resolve any build, runtime, or witness failures.',
        'Independent actual compositor connection attribution and schema descriptor compatibility audit.',
        'Both-host full Debug and runtime sanitizer qualification before any integration.'])
p = r / 'local/supervisor-world-handoff-candidate-01.json'
p.write_bytes((json.dumps(record, indent=2)+'\n').encode())
with tarfile.open(r / 'local/supervisor-world-handoff-candidate-01.tar', 'w') as archive:
    for name in sorted(files): archive.add(source / name, arcname=name)
print('Frozen', len(files), 'files;', len(record['changed']), 'changed/new;', len(locked), 'unchanged locks.')
