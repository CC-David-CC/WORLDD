import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
files=[]
fixture=root/'local/workspace-relay-barrier-validation-01'
for n in ['run.json','debug-fixed.log','runtime-sanitize-fixed.log','runtime-sanitize-baseline.log']:
    p=fixture/n;assert p.is_file();files.append((p,'barrier-fixture/'+host+'/'+n))
for n in ['debug-fixed.log','runtime-sanitize-fixed.log']:
    text=(fixture/n).read_text();assert re.search(r'Ran 20 tests in ',text) and '\nOK\n' in text
assert 'FAILED (failures=1)' in (fixture/'runtime-sanitize-baseline.log').read_text()
if host=='sb16':
    folder=root/'local/m02-participant-world-draft-01';source=folder/'source';manifest=json.loads((root/'local/participant-world-candidate-01.json').read_text())
    for n,h in manifest['files'].items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
    run=json.loads((folder/'run.json').read_text());assert [r['name'] for r in run['results']]==['configure','build','world','worker'] and all(r['exit_code']==0 for r in run['results'])
    for n,count in [('world.log',6),('worker.log',14)]:
        text=(folder/n).read_text();assert re.search(r'Ran '+str(count)+r' tests in ',text) and '100% tests passed, 0 tests failed' in text
    for n in ['run.json','configure.log','build.log','world.log','worker.log']:files.append((folder/n,'world-draft-01/'+host+'/'+n))
    files.append((root/'local/participant-world-candidate-01.json','world-draft-01/'+host+'/source-manifest.json'))
with tarfile.open(root/('local/world-progress-'+host+'-01.tar.gz'),'w:gz') as archive:
    for p,n in files:archive.add(p,arcname=n)
print(host,'collected',len(files),'selected evidence files',flush=True)
