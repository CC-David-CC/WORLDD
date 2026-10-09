import ast,hashlib,json,pathlib,tarfile
r=pathlib.Path(__file__).resolve().parents[1];source=r/'local/participant-workspace-draft-01'
prior=json.loads((r/'local/workspace-participant-candidate-01.json').read_text(encoding='utf-8'))['files']
files={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
assert set(files)-set(prior)=={'tests/core/test_workspace_participant_native.py'}
assert {n for n in prior if prior[n]!=files[n]}=={'CMakeLists.txt','tests/core/test_workspace_participant.py'}
for name in files:
    if name.endswith('.py'):ast.parse((source/name).read_text(encoding='utf-8'),filename=name)
out=r/'local/workspace-participant-candidate-02'
out.with_suffix('.json').write_text(json.dumps(dict(base='workspace01; identical production source',files=files),indent=2)+'\n',encoding='utf-8')
with tarfile.open(out.with_suffix('.tar'),'w') as archive:
    for name in sorted(files):archive.add(source/name,arcname=name,recursive=False)
print('250 files; only native test, CMake registration, and shared fixture changed')
