import hashlib,json,pathlib,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');assert socket.gethostname().split('.')[0]=='sb16'
files=[]
for n in ['participant-fixture-baseline-01.log','diagnose-participant-fixture-01.py','test-participant-fixtures-01.py','test-workspace-races-02.py']:
    files.append((r/'local'/n,'baseline-and-diagnostics/sb16/'+n))
for candidate in ['01','02']:
    root=r/('local/m02-participant-fixtures-'+candidate);manifest=json.loads((r/('local/participant-fixtures-candidate-'+candidate+'.json')).read_text())['files']
    for n,h in manifest.items():assert hashlib.sha256((root/'source'/n).read_bytes()).hexdigest()==h,n
    for path in (root/'debug').glob('*'):
        if path.is_file() and path.suffix in ('.log','.json'):files.append((path,'candidate-'+candidate+'/sb16/debug/'+path.name))
    files.append((r/('local/participant-fixtures-candidate-'+candidate+'.json'),'candidate-'+candidate+'/source-manifest.json'))
diag=r/'local/participant-fixture-diagnostic-01'
run=json.loads((diag/'run.json').read_text());assert run==dict(tests=8,failures=0,errors=0,skipped=0)
for p in diag.rglob('*'):
    if p.is_file() and p.suffix in ('.log','.json'):files.append((p,'diagnostic-01/sb16/'+p.relative_to(diag).as_posix()))
with tarfile.open(r/'local/participant-fixture-failures-03.tar.gz','w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print('Preserved fixture failures, two passing race checks, and eight passing diagnostic repetitions')
