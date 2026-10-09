import hashlib,json,pathlib,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');out=r/'local/m02-launch-authorization-baseline-01';source=out/'source'
manifest=json.loads((r/'local/launch-authorization-baseline-01.json').read_text())['files']
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for files in [manifest,locked]:
    for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
run=json.loads((out/'run.json').read_text());log=(out/'baseline.log').read_text()
assert [x['name'] for x in run['results']]==['configure','build','baseline'] and [x['exit_code'] for x in run['results']]==[0,0,1]
assert 'Ran 1 test' in log and 'FAILED (failures=1)' in log and 'UNKNOWN_METHOD' in log and 'No recognized issuer operation' in log
p=out/'summary.json';p.write_text(json.dumps(dict(host=host,source_files=len(manifest),locked_files=len(locked),
    baseline='missing launch authorization reproduced',feature_implemented=False,integrated=False),indent=2)+'\n')
with tarfile.open(r/('local/launch-authorization-baseline-'+host+'-01.tar.gz'),'w:gz') as a:
    for n in ['run.json','configure.log','build.log','baseline.log','baseline_driver.py','summary.json']:a.add(out/n,arcname='baseline-01/'+host+'/'+n)
print(host,'verified missing launch authorization baseline and frozen hashes')
