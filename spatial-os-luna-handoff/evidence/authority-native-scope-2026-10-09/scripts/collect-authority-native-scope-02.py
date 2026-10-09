import hashlib,json,re,socket,sys,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
mode=sys.argv[1];assert mode in ('baseline','debug','final')
items=[];summary=dict(host=host,mode=mode,candidate=2,integrated=False)
baseline=r/'local/m02-authority-native-scope-baseline-01'
run=json.loads((baseline/'run.json').read_text());assert [x['exit_code'] for x in run['results']]==[0,0,1]
text=(baseline/'baseline.log').read_text();assert 'Ran 2 tests' in text and 'FAILED (failures=2)' in text
assert text.count('Native authority ownership is not implemented')==2
summary['baseline_expected_failures']=2
for p in baseline.iterdir():
    if p.is_file():items.append((p,'baseline-01/'+host+'/'+p.name))
for name in ['authority-native-scope-baseline-01.json','authority-native-scope-candidate-01.json','authority-native-scope-candidate-02.json']:
    items.append((r/'local'/name,'manifests/'+name))
source=r/'local/m02-authority-native-scope-02/source'
manifest=json.loads((r/'local/authority-native-scope-candidate-02.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for n,h in locked.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
summary['source_files']=len(manifest);summary['locked_files']=len(locked)
failed=r/'local/m02-authority-native-scope-01/debug'
run=json.loads((failed/'run.json').read_text());assert [x['exit_code'] for x in run['results']]==[0,0,0,8]
text=(failed/'scope.log').read_text();assert 'Ran 11 tests' in text and 'FAILED (errors=1)' in text and 'X probe terminated' in text
for name in ['configure.log','build.log','harness.log','scope.log','run.json']:items.append((failed/name,'candidate-01-failure/'+host+'/'+name))
diag=r/'local/native-scope-input-diagnostic-01';run=json.loads((diag/'run.json').read_text());assert [x['exit_code'] for x in run['results']]==[1,0]
for p in diag.iterdir():
    if p.is_file():items.append((p,'input-diagnostic-01/'+host+'/'+p.name))

for profile in ([] if mode=='baseline' else ['debug']+(['runtime-sanitize'] if mode=='final' else [])):
    folder=source.parent/profile;run=json.loads((folder/'run.json').read_text())
    assert [x['name'] for x in run['results']]==['configure','build','harness','scope']
    assert all(x['exit_code']==0 for x in run['results'])
    text=(folder/'scope.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
    assert len(counts)==3 and sorted(counts)==[11,21,22] and '100% tests passed, 0 tests failed out of 3' in text
    assert '... skipped' not in text and 'skipped=' not in text
    text=(folder/'harness.log').read_text();assert 'Ran 51 tests' in text and text.rstrip().endswith('OK')
    summary[profile]=dict(checks=54,suites=3,new_checks=11,regression_checks=43,harness_checks=51,failed=0,skipped=0)
    for p in folder.iterdir():
        if p.is_file():items.append((p,'candidate-02/'+host+'/'+profile+'/'+p.name))
    for p in (folder/'server').rglob('*.log'):items.append((p,'candidate-02/'+host+'/'+profile+'/server/'+p.name))
p=r/f'local/authority-native-scope-{host}-{mode}-02-summary.json';p.write_text(json.dumps(summary,indent=2)+'\n')
items.append((p,host+'-'+mode+'-summary.json'))
with tarfile.open(r/f'local/authority-native-scope-{host}-{mode}-02.tar.gz','w:gz') as a:
    for p,n in items:a.add(p,arcname=n)
print(json.dumps(summary))
