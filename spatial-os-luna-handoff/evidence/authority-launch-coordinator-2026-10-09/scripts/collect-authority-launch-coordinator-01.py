import hashlib,json,pathlib,re,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=r/'local/m02-authority-launch-coordinator-01/source'
manifest=json.loads((r/'local/authority-launch-coordinator-candidate-01.json').read_text())['files']
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for files in [manifest,locked]:
    for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
files=[];summary=dict(host=host,candidate=1,source_files=len(manifest),locked_files=len(locked),integrated=False,
    limitation='Additional failed prepared-commit regression excludes candidate01 from integration')
for label,folder,marker in [('durability-baseline-01','m02-authority-launch-durability-baseline-01','Predecessor released execution without a durable launch record'),
    ('partial-commit-baseline-01','m02-authority-launch-partial-baseline-01','Invalid native cleanup record')]:
    out=r/'local'/folder;run=json.loads((out/'run.json').read_text());text=(out/'baseline.log').read_text()
    assert run['exit_code']==1 and 'Ran 1 test' in text and 'FAILED (failures=1)' in text and marker in text
    for n in ['run.json','baseline.log']:files.append((out/n,label+'/'+host+'/'+n))
for profile in ['debug','runtime-sanitize']:
    out=source.parent/profile;run=json.loads((out/'run.json').read_text())
    assert [x['name'] for x in run['results']]==['configure','build','harness','focused'] and all(x['exit_code']==0 for x in run['results'])
    text=(out/'focused.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
    assert len(counts)==3 and sum(counts)==28 and '100% tests passed, 0 tests failed out of 3' in text
    assert '... skipped' not in text and 'skipped=' not in text
    harness=(out/'harness.log').read_text();assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    summary[profile]=dict(focused_checks=28,suites=3,harness_checks=51,failed=0,skipped=0,
        coordinator_binary_sha256=hashlib.sha256((out/'build/spatial-authority-launch-probe').read_bytes()).hexdigest())
    for n in ['run.json','configure.log','build.log','harness.log','focused.log']:files.append((out/n,'candidate-01/'+host+'/'+profile+'/'+n))
p=r/('local/authority-launch-coordinator-summary-'+host+'-01.json');p.write_text(json.dumps(summary,indent=2)+'\n')
files.append((p,'candidate-01/'+host+'/summary.json'))
with tarfile.open(r/('local/authority-launch-coordinator-'+host+'-01.tar.gz'),'w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print(host,'270 source hashes; both focused profiles and both original failures verified',flush=True)
