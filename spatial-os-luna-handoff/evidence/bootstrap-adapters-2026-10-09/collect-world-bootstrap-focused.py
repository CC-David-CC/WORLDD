import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
source=root/'local/m01-world-bootstrap-draft-01/source'
manifest=json.loads((source/'source-manifest.json').read_text())
for name,digest in manifest.items(): assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
summary={};files=[]
for label in ('debug','runtime-sanitize'):
    out=root/('local/m01-world-bootstrap-focused-'+label+'-01')
    report=json.loads((out/'run.json').read_text())
    assert [r['name'] for r in report['results']]==['configure','build','regressions','races']
    assert all(r['exit_code']==0 for r in report['results'])
    log=(out/'regressions.log').read_text();race=(out/'races.log').read_text()
    assert '100% tests passed, 0 tests failed out of 4' in log
    counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
    assert len(counts)==4
    names=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M)
    assert set(names)=={'world-bootstrap','world-identity','world-authority','workspace-authority'}
    assert re.search(r'Ran 6 tests in ',race) and '\nOK\n' in race
    assert 'skipped=' not in log+race and '... skipped ' not in log+race
    summary[label]={'suites':dict(zip(names,counts)),'regression_checks':sum(counts),
        'additional_checks':6,'failed':0,'skipped':0,
        'binaries':{name:hashlib.sha256((out/'build'/name).read_bytes()).hexdigest()
            for name in ('identityd','spatial-issuer-client','worldd','workspaced','spatialwm')}}
    for path in out.iterdir():
        if path.is_file() and path.suffix in ('.json','.log','.py'):
            files.append((path,'sb16/'+out.name+'/'+path.name))
summary.update(host='sb16',source_files=len(manifest),scope='Isolated focused world adapter qualification',
    limitations=['Full cumulative matrix not run for adapter','Second-host adapter qualification pending',
        'Workspace bootstrap delivery unimplemented','Native-server GLX sanitizer unqualified','No adapter production integration'])
record=root/'local/world-bootstrap-focused-summary.json';record.write_text(json.dumps(summary,indent=2)+'\n')
files.append((record,'sb16/world-focused-summary.json'))
with tarfile.open(root/'local/world-bootstrap-focused-evidence-sb16.tar.gz','w:gz') as archive:
    for path,name in files: archive.add(path,arcname=name)
print(json.dumps(summary),flush=True)
