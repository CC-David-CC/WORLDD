import hashlib,json,pathlib,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');assert socket.gethostname().split('.')[0]=='sb16'
manifest=json.loads((r/'local/participant-fixtures-candidate-03.json').read_text())['files'];files=[]
for run in ['03','04']:
    root=r/('local/m02-participant-fixtures-'+run)
    for n,h in manifest.items():assert hashlib.sha256((root/'source'/n).read_bytes()).hexdigest()==h,n
    for n in ['run.json','configure.log','build.log','harness.log','focused.log']:
        p=root/'debug'/n
        if p.exists():files.append((p,('candidate-03/sb16/debug/' if run=='03' else 'mispackaged-run-04/sb16/')+n))
run=json.loads((r/'local/m02-participant-fixtures-03/debug/run.json').read_text());assert [x['exit_code'] for x in run['results']]==[0,0,0,8]
log=(r/'local/m02-participant-fixtures-03/debug/focused.log').read_text();assert 'Ran 20 tests' in log and '88% tests passed, 1 tests failed out of 8' in log
files.append((r/'local/participant-fixtures-candidate-03.json','candidate-03/source-manifest.json'))
for n in ['qualify-participant-fixtures-04.py','stop-mispackaged-fixture-run-04.py']:files.append((r/'local'/n,'mispackaged-run-04/'+n))
with tarfile.open(r/'local/participant-fixtures-03-evidence.tar.gz','w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print('Verified candidate03 and mispackaged source04 were both candidate03; partial run04 is not qualification')
