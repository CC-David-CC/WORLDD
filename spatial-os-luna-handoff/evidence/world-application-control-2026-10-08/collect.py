import pathlib,shutil,json,re,socket,tarfile,hashlib
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
target=p/'local/world-application-control-evidence-export';target.mkdir(exist_ok=False)
for source in sorted((p/'local').glob('m04-world-application-control-*')):
    if not source.is_dir():continue
    dest=target/source.name;dest.mkdir()
    for f in source.iterdir():
        if f.is_file() and f.suffix in {'.json','.log','.txt'}:shutil.copy2(f,dest/f.name)
    server=source/'server'
    if server.exists():
        for f in server.rglob('*.log'):
            out=dest/'server'/f.relative_to(server);out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,out)
summary={}
for label in ['debug','runtime-sanitize']:
    d=target/('m04-world-application-control-'+label+'-02')
    run=json.loads((d/'run.json').read_text())
    assert [r['name'] for r in run['results']]==['configure','build','harness','core','P10']
    assert all(r['exit_code']==0 for r in run['results'])
    text=(d/'core.log').read_text();counts=list(map(int,re.findall(r'Ran (\d+) tests? in',text)))
    assert len(counts)==45 and sum(counts)==891,(len(counts),sum(counts))
    assert '100% tests passed, 0 tests failed out of 45' in text
    assert not re.search(r'OK \(skipped=|skipped=|\*\*\*Failed',text)
    harness=(d/'harness.log').read_text();assert 'Ran 51 tests' in harness and 'OK' in harness
    gate=json.loads((d/'P10.json').read_text())
    assert gate['status']=='passed' and gate['selected']==151 and gate['executed']==151
    assert len(gate['tests'])==151 and all(t['status']=='passed' for t in gate['tests'])
    summary[label]=dict(core_suites=45,core_checks=891,harness_checks=51,acceptance_checks=151,failed=0,skipped=0)
focused=target/'m04-world-application-control-focused-06'
assert json.loads((focused/'run.json').read_text())['exit_code']==0
counts=list(map(int,re.findall(r'Ran (\d+) tests? in',(focused/'test.log').read_text())))
assert sorted(counts)==[16,20,25],counts
summary['focused']=dict(checks=61,failed=0,skipped=0)
summary['native-server-sanitize']=dict(status='unrun',reason='These world tests require GLX. The existing instrumented GLX cleanup issue remains unqualified.')
manifest=json.loads((p/'local/world-control-tested-inputs.json').read_text())
actual={f:hashlib.sha256((p.parent/f).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for f in manifest}
assert actual==manifest,'Tested source differs from reviewed inputs'
(target/'tested-inputs.json').write_text(json.dumps(actual,indent=2)+'\n')
(target/'binaries.json').write_text(json.dumps({b:{name:hashlib.sha256((p/b/name).read_bytes()).hexdigest() for name in ['worldd','spatialwm','workspaced']} for b in ['build','build-sanitize']},indent=2)+'\n')
(target/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(p/'local/world-application-control-evidence.tar','w') as tar:
    for f in sorted(target.rglob('*')):
        if f.is_file():tar.add(f,arcname=str(f.relative_to(target)))
print(socket.gethostname(),json.dumps(summary),flush=True)
