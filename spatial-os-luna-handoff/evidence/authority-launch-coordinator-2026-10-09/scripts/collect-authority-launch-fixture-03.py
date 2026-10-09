import hashlib,json,pathlib,re,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=r/'local/m02-authority-launch-coordinator-02/source'
two=json.loads((r/'local/authority-launch-coordinator-candidate-02.json').read_text())['files']
three=json.loads((r/'local/authority-launch-coordinator-candidate-03.json').read_text())['files']
assert {n for n in two if two[n]!=three[n]}=={'tests/native/test_authority_launch.py'}
for n,h in two.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
files=[];out=source.parent/'debug';run=json.loads((out/'run.json').read_text())
assert [x['name'] for x in run['results']]==['configure','build','harness','focused']
assert [x['exit_code'] for x in run['results']]==[0,0,0,8]
text=(out/'focused.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
assert len(counts)==4 and sum(counts)==43 and text.count('FAILED (failures=1)')==1 and 'cgroup.events' in text
assert 'test_invalid_binding_cannot_create_an_external_scope' in text
for n in ['run.json','configure.log','build.log','harness.log','focused.log']:files.append((out/n,'candidate-02/'+host+'/debug/'+n))
out=r/'local/m02-authority-launch-fixture-03';run=json.loads((out/'run.json').read_text());text=(out/'fixture.log').read_text()
assert run['exit_code']==0 and 'Ran 14 tests' in text and '\nOK\n' in text
assert run['test_sha256']==three['tests/native/test_authority_launch.py']
for n in ['run.json','fixture.log']:files.append((out/n,'fixture-correction-03/'+host+'/'+n))
summary=dict(host=host,candidate02=dict(debug_focused_checks=43,passed=42,failed=1,skipped=0,full_core='unrun',P10='unrun',runtime_sanitize='unrun'),
    fixture03=dict(checks=14,passed=14,failed=0,skipped=0,production_inputs='identical to candidate02',test_sha256=run['test_sha256']))
p=r/('local/authority-launch-fixture-summary-'+host+'-03.json');p.write_text(json.dumps(summary,indent=2)+'\n');files.append((p,'fixture-correction-03/'+host+'/summary.json'))
with tarfile.open(r/('local/authority-launch-fixture-'+host+'-03.tar.gz'),'w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print(host,'original fixture failure and unchanged-production correction verified',flush=True)
