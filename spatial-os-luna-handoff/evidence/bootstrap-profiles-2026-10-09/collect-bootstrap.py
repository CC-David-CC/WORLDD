import hashlib,json,pathlib,re,socket,tarfile

p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
summary={}
for label in ('debug','runtime-sanitize'):
    root=p/('local/m01-bootstrap-'+label+'-02')
    report=json.loads((root/'run.json').read_text())
    assert [r['name'] for r in report['results']]==['configure','build','harness','core','P10']
    assert all(r['exit_code']==0 for r in report['results']), label
    core=(root/'core.log').read_text()
    counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',core,re.M)]
    assert len(counts)==49 and sum(counts)==941, (label,len(counts),sum(counts))
    assert '100% tests passed, 0 tests failed out of 49' in core
    assert not re.search(r'OK \(skipped=|\.\.\. skipped ',core)
    harness=(root/'harness.log').read_text()
    assert re.search(r'Ran 51 tests in ',harness) and 'OK' in harness and 'skipped=' not in harness
    acceptance=json.loads((root/'P10.json').read_text())
    assert acceptance['status']=='passed' and acceptance['executed']==acceptance['selected']==151
    assert len(acceptance['tests'])==151 and all(t['status']=='passed' for t in acceptance['tests'])
    summary[label]=dict(core_suites=49,core_checks=sum(counts),harness_checks=51,acceptance_checks=151,failed=0,skipped=0)
summary['native-server-sanitize']=dict(status='unrun',reason='Native-server GLX cleanup remains unqualified; runtime sanitizer results do not replace it.')
summary_file=p/'local/m01-bootstrap-summary.json'
summary_file.write_text(json.dumps(summary,indent=2)+'\n')
files=[]
for directory in ('m01-bootstrap-baseline-01','m01-bootstrap-focused-01','m01-bootstrap-debug-01','m01-bootstrap-runtime-sanitize-01',
                  'm01-bootstrap-debug-02','m01-bootstrap-runtime-sanitize-02','m01-display-ready-native-focused-01'):
    root=p/'local'/directory
    if not root.exists(): continue
    for file in root.iterdir():
        if file.is_file() and (file.suffix in ('.log','.json','.proto','.descriptor')):
            files.append((file,directory+'/'+file.name))
for name in ('m01-bootstrap-client-baseline.log','m01-bootstrap-client-build.log','m01-bootstrap-client-focused.log',
             'm01-bootstrap-audit.json','bootstrap-tested-tree.json','bootstrap-focused-inputs.json',
             'm01-bootstrap-audit-02.json','bootstrap-tested-tree-02.json','m01-display-ready-baseline.log','m01-display-ready-focused.log'):
    file=p/'local'/name
    if file.exists(): files.append((file,name))
result={}
for build in ('build','build-sanitize'):
    result[build]={}
    for name in ('identityd','spatial-issuer-client','worldd','workspaced','spatialwm','spatial-test-host'):
        file=p/build/name
        if file.exists(): result[build][name]=hashlib.sha256(file.read_bytes()).hexdigest()
metadata=p/'local/m01-bootstrap-binaries.json'
metadata.write_text(json.dumps(result,indent=2)+'\n')
files.append((metadata,'binaries.json'))
files.append((summary_file,'summary.json'))
with tarfile.open(p/('local/bootstrap-evidence-'+host+'.tar.gz'),'w:gz') as archive:
    for file,name in files: archive.add(file,arcname=host+'/'+name)
print(host,len(files),'evidence files; no databases, private keys, or binaries',flush=True)
