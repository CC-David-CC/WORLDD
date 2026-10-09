import ast,hashlib,json,pathlib,tarfile
r=pathlib.Path(__file__).resolve().parents[1];s=r/'local/participant-fixtures-draft-01'
prior=json.loads((r/'local/participant-fixtures-candidate-01.json').read_text(encoding='utf-8'))['files']
files={p.relative_to(s).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in s.rglob('*') if p.is_file()}
assert set(files)-set(prior)=={'tests/core/test_workspace_participant_races.py'}
assert {n for n in prior if files[n]!=prior[n]}=={'CMakeLists.txt','tests/core/test_workspace_participant.py'}
for n in files:
    if n.endswith('.py'):ast.parse((s/n).read_text(encoding='utf-8'),filename=n)
(r/'local/participant-fixtures-candidate-02.json').write_text(json.dumps(dict(base='participant-fixtures-candidate-01',files=files,production_source='unchanged workspace02'),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(r/'local/participant-fixtures-candidate-02.tar','w') as a:
    for n in sorted(files):a.add(s/n,arcname=n,recursive=False)
print('251 files; five new domain and delayed-reply tests; production unchanged')
