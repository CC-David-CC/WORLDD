import hashlib,json,os,re,socket,subprocess,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
source=r/'local/m02-authority-prepared-launch-01/source'
manifest=json.loads((r/'local/authority-prepared-launch-candidate-01.json').read_text())['files']
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for files in (manifest,locked):
    for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
items=[];summary=dict(host=host,candidate=1,source_files=len(manifest),locked_files=len(locked),integrated=False)
baseline=r/'local/m02-authority-prepared-launch-baseline-01'
run=json.loads((baseline/'run.json').read_text());assert [x['exit_code'] for x in run['results']]==[0,0,1]
text=(baseline/'baseline.log').read_text();assert 'Ran 2 tests' in text and 'FAILED (failures=2)' in text
assert text.count('code: INVALID_STATE')==2
for p in baseline.iterdir():
    if p.is_file():items.append((p,'baseline-01/'+host+'/'+p.name))
for profile in ['debug','runtime-sanitize']:
    folder=source.parent/profile;run=json.loads((folder/'run.json').read_text())
    assert [x['name'] for x in run['results']]==['configure','build','scope']
    assert all(x['exit_code']==0 for x in run['results'])
    text=(folder/'scope.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
    assert sorted(counts)==[8,10] and '100% tests passed, 0 tests failed out of 2' in text
    assert '... skipped' not in text and 'skipped=' not in text
    if not (folder/'harness-run.json').exists():
        command=['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']
        with (folder/'harness.log').open('wb') as log:
            result=subprocess.run(command,cwd=source,env=dict(os.environ,PYTHONPATH=str(folder/'build/python')+':'+str(source)),stdout=log,stderr=subprocess.STDOUT)
        (folder/'harness-run.json').write_text(json.dumps(dict(command=command,exit_code=result.returncode),indent=2)+'\n')
    assert json.loads((folder/'harness-run.json').read_text())['exit_code']==0
    text=(folder/'harness.log').read_text();assert 'Ran 51 tests' in text and text.rstrip().endswith('OK')
    summary[profile]=dict(checks=18,suites=2,new_checks=8,regression_checks=10,harness_checks=51,failed=0,skipped=0)
    for p in folder.iterdir():
        if p.is_file():items.append((p,'candidate-01/'+host+'/'+profile+'/'+p.name))
for name in ['authority-prepared-launch-baseline-01.json','authority-prepared-launch-candidate-01.json']:
    items.append((r/'local'/name,'manifests/'+name))
p=r/f'local/prepared-launch-{host}-summary-01.json';p.write_text(json.dumps(summary,indent=2)+'\n');items.append((p,host+'-summary.json'))
with tarfile.open(r/f'local/prepared-launch-{host}-01.tar.gz','w:gz') as a:
    for p,n in items:a.add(p,arcname=n)
print(json.dumps(summary))
