import hashlib,io,json,pathlib,re,subprocess
r=pathlib.Path(__file__).resolve().parents[1];repo=r.parent;e=r/'evidence/process-termination-2026-10-09'
manifest=json.loads((e/'integration-applied-02.json').read_text(encoding='utf-8'))['files'];locked=json.loads((r/'acceptance.lock.json').read_text(encoding='utf-8'))['files']
names=list(filter(None,subprocess.check_output(['git','diff','--cached','--name-only','-z'],cwd=repo).decode().split('\0')))
all_names=sorted(set(names)|{'spatial-os-luna-handoff/'+n for n in manifest}|{'spatial-os-luna-handoff/'+n for n in locked})
stream=io.BytesIO(subprocess.check_output(['git','cat-file','--batch'],cwd=repo,input=''.join(':'+n+'\n' for n in all_names).encode()));blobs={}
for n in all_names:
    header=stream.readline().split();assert len(header)==3 and header[1]==b'blob',n
    blobs[n]=stream.read(int(header[2]));assert stream.read(1)==b'\n'
for files in [manifest,locked]:
    for n,h in files.items():assert hashlib.sha256(blobs['spatial-os-luna-handoff/'+n]).hexdigest()==h,n
for host in ['sb16','sb16-2']:
    folder=e/'candidate-02'/host;summary=json.loads((folder/'summary-final.json').read_text(encoding='utf-8'))
    assert json.loads((folder/'source-manifest.json').read_text(encoding='utf-8'))['files']==manifest
    for profile in ['debug','runtime-sanitize']:
        result=summary[profile];assert tuple(result[k] for k in ['core_checks','core_suites','harness_checks','P10_cases','failed','skipped'])==(1192,69,51,151,0,0)
        for part,suites,count in [('termination',1,6),('core',68,1186)]:
            text=(folder/profile/(part+'.log')).read_text(encoding='utf-8');counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
            assert len(counts)==suites and sum(counts)==count and f'100% tests passed, 0 tests failed out of {suites}' in text and 'skipped' not in text
        report=json.loads((folder/profile/'P10.json').read_text(encoding='utf-8'));assert report['status']=='passed' and report['executed']==len(report['tests'])==151 and all(x['status']=='passed' for x in report['tests'])
for n in names:
    assert not n.startswith('defect blockers/'),n
    assert pathlib.PurePosixPath(n).suffix not in ('.db','.sqlite','.sqlite3','.pem','.key','.tar','.gz'),n
    data=blobs[n]
    if n.endswith('.json'):json.loads(data)
    for pattern in (rb'-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY)-----',rb'Bearer\s+[A-Za-z0-9_-]{20,}',rb'fleet-[a-z0-9]+-[a-f0-9]{20,}'):
        assert not re.search(pattern,data),n
print(json.dumps(dict(source_files=242,locked_files=41,full_both_host_profiles='passed',credential_exclusions='passed',staged_files=len(names))))
