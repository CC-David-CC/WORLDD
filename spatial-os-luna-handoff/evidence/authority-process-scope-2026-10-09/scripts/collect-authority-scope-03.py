import hashlib,json,re,socket,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
files=[];summary={'host':host,'integrated':False,'candidates':{}}
for candidate in (1,2,3):
    label=f'{candidate:02}'
    root=r/f'local/m02-authority-process-scope-{label}'
    manifest=json.loads((r/f'local/authority-process-scope-candidate-{label}.json').read_text())['files']
    for n,h in manifest.items():assert hashlib.sha256((root/'source'/n).read_bytes()).hexdigest()==h
    result={}
    for profile in ('debug','runtime-sanitize'):
        folder=root/profile
        if not folder.exists():continue
        run=json.loads((folder/'run.json').read_text());result[profile]=run['results']
        for p in folder.glob('*.log'):files.append((p,f'candidate-{label}/{host}/{profile}/{p.name}'))
        files.append((folder/'run.json',f'candidate-{label}/{host}/{profile}/run.json'))
        if (folder/'cases').exists():
            for p in (folder/'cases').rglob('*.log'):files.append((p,f'candidate-{label}/{host}/{profile}/cases/'+p.relative_to(folder/'cases').as_posix()))
        if candidate==3:
            assert [x['name'] for x in run['results']]==['configure','build','scope']
            assert all(x['exit_code']==0 for x in run['results'])
            log=(folder/'scope.log').read_text();assert 'Ran 10 tests' in log and '\nOK\n' in log and 'skipped' not in log
            result[profile]={'checks':10,'failed':0,'skipped':0,'binary_sha256':hashlib.sha256((folder/'build/spatial-process-scope-probe').read_bytes()).hexdigest()}
    summary['candidates'][str(candidate)]=result
    files.append((r/f'local/authority-process-scope-candidate-{label}.json',f'candidate-{label}/{host}/source-manifest.json'))
diagnostic=r/'local/scope-filter-diagnostic-01'
if diagnostic.exists():
    for p in diagnostic.iterdir():files.append((p,'filter-diagnostic/'+host+'/'+p.name))
out=r/f'local/authority-scope-summary-{host}-03.json';out.write_text(json.dumps(summary,indent=2)+'\n');files.append((out,host+'-summary.json'))
with tarfile.open(r/f'local/authority-scope-evidence-{host}-03.tar.gz','w:gz') as archive:
    for p,n in files:archive.add(p,arcname=n)
print(host,'verified source manifests and candidate03 ten tests per profile')
