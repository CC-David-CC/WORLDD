import hashlib,json,pathlib,re,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=r/'local/m02-workspace-participant-focus-02/source';manifest=json.loads((r/'local/workspace-participant-candidate-02.json').read_text())['files'];locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for files in [manifest,locked]:
    for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
files=[];summary=dict(host=host,source_files=len(manifest),locked_files=len(locked),integrated=False)
for profile in ['debug','runtime-sanitize']:
    out=source.parent/profile;run=json.loads((out/'run.json').read_text());assert [x['name'] for x in run['results']]==['configure','build','harness','focused'] and all(x['exit_code']==0 for x in run['results'])
    log=(out/'focused.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
    assert sorted(counts)==[2,2,6,6,6,14] and '100% tests passed, 0 tests failed out of 6' in log and 'skipped' not in log
    names=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M);assert len(set(names))==6
    harness=(out/'harness.log').read_text();assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    summary[profile]=dict(checks=36,suites=dict(zip(names,counts)),harness=51,failed=0,skipped=0,binaries={n:hashlib.sha256((out/'build'/n).read_bytes()).hexdigest() for n in ['workspaced','worldd','identityd','spatialwm','spatial-app-init','spatial-test-host']})
    for n in ['run.json','configure.log','build.log','harness.log','focused.log']:files.append((out/n,'focused-02/'+host+'/'+profile+'/'+n))
p=r/('local/workspace-focus-'+host+'-02.json');p.write_text(json.dumps(summary,indent=2)+'\n');files.append((p,'focused-02/'+host+'/summary.json'))
files.append((r/'local/workspace-participant-candidate-02.json','focused-02/'+host+'/source-manifest.json'))
with tarfile.open(r/('local/workspace-focus-'+host+'-02.tar.gz'),'w:gz') as archive:
    for p,n in files:archive.add(p,arcname=n)
print(host,'Debug and runtime sanitizers verified: 36 checks and51 harness each')
