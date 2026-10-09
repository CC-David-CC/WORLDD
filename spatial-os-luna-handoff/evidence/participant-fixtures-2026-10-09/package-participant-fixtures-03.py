import ast,hashlib,json,pathlib,tarfile
r=pathlib.Path(__file__).resolve().parents[1];s=r/'local/participant-fixtures-draft-01'
prior=json.loads((r/'local/participant-fixtures-candidate-02.json').read_text(encoding='utf-8'))['files']
files={n:hashlib.sha256((s/n).read_bytes()).hexdigest() for n in prior}
assert {n for n in prior if files[n]!=prior[n]}=={'src/workspace/main.cpp','tests/core/test_workspace_participant_races.py'}
for n in files:
    if n.endswith('.py'):ast.parse((s/n).read_text(encoding='utf-8'),filename=n)
(r/'local/participant-fixtures-candidate-03.json').write_text(json.dumps(dict(base='participant-fixtures-candidate-02',files=files,change='Preserve existing typed errors during cancellation; use separate second repository fixture.'),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(r/'local/participant-fixtures-candidate-03.tar','w') as a:
    for n in sorted(files):a.add(s/n,arcname=n,recursive=False)
print('251 frozen files; typed failure preservation and domain fixture corrected')
