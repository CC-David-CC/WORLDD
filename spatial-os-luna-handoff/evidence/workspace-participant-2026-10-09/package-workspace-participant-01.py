import ast,hashlib,json,pathlib,tarfile
root=pathlib.Path(__file__).resolve().parents[1];source=root/'local/participant-workspace-draft-01'
files=sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())
assert len(files)==249,len(files)
for name in files:
    if name.endswith('.py'):ast.parse((source/name).read_text(encoding='utf-8'),filename=name)
locked=json.loads((source/'acceptance.lock.json').read_text(encoding='utf-8'))['files']
digests={name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in files}
for name,digest in locked.items():assert digests[name]==digest,name
out=root/'local/workspace-participant-candidate-01'
out.with_suffix('.json').write_text(json.dumps(dict(base='world03 + process02',files=digests),indent=2)+'\n',encoding='utf-8')
with tarfile.open(out.with_suffix('.tar'),'w') as archive:
    for name in files:archive.add(source/name,arcname=name,recursive=False)
print(len(files),'source files;',len(locked),'locked files preserved')
