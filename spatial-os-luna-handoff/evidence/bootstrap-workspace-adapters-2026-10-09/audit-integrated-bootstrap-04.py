import ast,hashlib,io,json,pathlib,re,subprocess
repo=pathlib.Path(__file__).resolve().parents[2];root=repo/'spatial-os-luna-handoff'
prepared=json.loads((root/'local/adapter-integration-applied-04.json').read_text())
locked=json.loads((root/'acceptance.lock.json').read_text())['files']
names=list(filter(None,subprocess.check_output(['git','diff','--cached','--name-only','-z'],cwd=repo).decode().split('\0')))
all_names=sorted(set(names)|{'spatial-os-luna-handoff/'+n for n in prepared['files']}|{'spatial-os-luna-handoff/'+n for n in locked})
stream=io.BytesIO(subprocess.check_output(['git','cat-file','--batch'],cwd=repo,input=''.join(':'+n+'\n' for n in all_names).encode()))
blobs={}
for name in all_names:
    header=stream.readline().split();assert len(header)==3 and header[1]==b'blob',name
    blobs[name]=stream.read(int(header[2]));assert stream.read(1)==b'\n'
assert stream.read()==b''
for files in (prepared['files'],locked):
    for name,digest in files.items():assert hashlib.sha256(blobs['spatial-os-luna-handoff/'+name]).hexdigest()==digest,name
for host in ('sb16','sb16-2'):
    base=root/'evidence/bootstrap-workspace-adapters-2026-10-09'/host
    assert json.loads((base/'finish-04/integration-manifest.json').read_text())['files']==prepared['files']
    summary=json.loads((base/'summary-final-04.json').read_text())
    for profile in ('debug','runtime-sanitize'):
        v=summary[profile];assert (v['core_checks'],v['core_suites'],v['harness_checks'],v['P10_cases'],v['failed'],v['skipped'])==(1037,55,51,151,0,0)
    assert json.loads((base/'finish-04/run.json').read_text())['status']=='passed'
def bodies(text):
    cls=next(n for n in ast.parse(text).body if isinstance(n,ast.ClassDef) and n.name=='ProcessTests')
    return {n.name:ast.dump(n,include_attributes=False) for n in cls.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
process='spatial-os-luna-handoff/tests/core/test_processes.py'
old=subprocess.check_output(['git','show','c470a4e079b0e07009af5fcff7b491214bf63bec:'+process],cwd=repo).decode()
assert bodies(old)==bodies(blobs[process].decode()) and len(bodies(old))==12
for name in names:
    assert not name.startswith('defect blockers/'),name
    assert pathlib.PurePosixPath(name).suffix not in ('.db','.sqlite','.sqlite3','.pem','.key','.tar','.gz'),name
    data=blobs[name]
    if name.endswith('.json'):json.loads(data)
    for pattern in (rb'-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY)-----',rb'Bearer\s+[A-Za-z0-9_-]{20,}',rb'fleet-[a-z0-9]+-[a-f0-9]{20,}'):
        assert not re.search(pattern,data),name
record={'source_files':len(prepared['files']),'locked_files':len(locked),'unchanged_original_process_tests':12,
    'both_host_profiles':'passed','credential_exclusions':'passed','staged_files':len(names)}
(root/'local/adapter-staged-audit-04.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record))
