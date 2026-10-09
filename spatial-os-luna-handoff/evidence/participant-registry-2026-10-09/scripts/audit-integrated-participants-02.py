"""Check staged production bytes against both complete candidate qualifications."""
import hashlib,io,json,pathlib,re,subprocess
repo=pathlib.Path(__file__).resolve().parents[2];root=repo/'spatial-os-luna-handoff'
evidence=root/'evidence/participant-registry-2026-10-09'
prepared=json.loads((root/'local/participant-integration-applied-02.json').read_text())
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
    base=evidence/'candidate-02'/host
    assert json.loads((base/'source-manifest.json').read_text())['files']==prepared['files']
    summary=json.loads((base/'summary-final.json').read_text())
    for profile in ('debug','runtime-sanitize'):
        value=summary[profile]
        assert tuple(value[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped'))==(1120,64,51,151,0,0)
    audit=json.loads((base/'schema-audit.json').read_text())
    assert audit['status']=='passed' and audit['unchanged_existing_test_files']==84 and audit['locked_files']==41
for name in names:
    assert not name.startswith('defect blockers/'),name
    assert pathlib.PurePosixPath(name).suffix not in ('.db','.sqlite','.sqlite3','.pem','.key','.tar','.gz'),name
    data=blobs[name]
    if name.endswith('.json'):json.loads(data)
    for pattern in (rb'-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY)-----',rb'Bearer\s+[A-Za-z0-9_-]{20,}',rb'fleet-[a-z0-9]+-[a-f0-9]{20,}'):
        assert not re.search(pattern,data),name
record={'source_files':len(prepared['files']),'locked_files':len(locked),'both_host_profiles':'passed',
    'registry_qualification':'passed','credential_exclusions':'passed','staged_files':len(names)}
(root/'local/participant-staged-audit-02.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record))
