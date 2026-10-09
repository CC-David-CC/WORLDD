import hashlib,json,pathlib,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/m02-participant-relay-baseline-01';source=out/'source';folder=out/'debug'
manifest=json.loads((r/'local/participant-relay-baseline-01.json').read_text())['files']
for names in [manifest,json.loads((source/'acceptance.lock.json').read_text())['files']]:
    for n,h in names.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
run=json.loads((folder/'run.json').read_text());assert [v['exit_code'] for v in run['results']]==[0,0,0,8]
log=(folder/'baseline.log').read_text();assert 'Unknown launch operation' in log and 'Ran 1 test' in log and 'FAILED (failures=1)' in log
harness=(folder/'harness.log').read_text();assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
summary=dict(host=host,source_files=len(manifest),locked_files=41,missing_operation='prepare_channel',expected_failures=1,harness_checks=51,acceptance_pass=False)
p=out/'summary.json';p.write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(r/('local/participant-relay-baseline-'+host+'-01.tar.gz'),'w:gz') as a:
    for name in ['run.json','configure.log','build.log','harness.log','baseline.log']:a.add(folder/name,arcname='baseline-01/'+host+'/'+name)
    a.add(p,arcname='baseline-01/'+host+'/summary.json')
print(host,'verified expected missing relay operation,277sources41locks51harness',flush=True)
