import hashlib,json,pathlib,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');out=r/'local/m02-owned-participant-baseline-01';source=out/'source'
manifest=json.loads((r/'local/owned-participant-baseline-01.json').read_text())['files']
locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for files in [manifest,locked]:
    for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
run=json.loads((out/'debug/run.json').read_text());assert [x['exit_code'] for x in run['results']]==[0,0,0,8]
log=(out/'debug/baseline.log').read_text()
assert 'Ran 2 tests' in log and 'FAILED (failures=2)' in log
assert 'FAIL: test_supervised_child_cannot_execute_without_durable_authorization' in log
assert 'stage: START_ATTEMPTED' in log and 'Unknown launch operation' in log
summary=dict(host=host,source_files=len(manifest),locked_files=len(locked),baseline_failures=2,build='passed',harness_checks=51,
    result='The old coordinator accepted a supervised start without approval and lacked participant attachment.',integrated=False)
p=out/'summary.json';p.write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(r/('local/owned-participant-baseline-'+host+'-01.tar.gz'),'w:gz') as a:
    for n in ['run.json','configure.log','build.log','harness.log','baseline.log']:a.add(out/'debug'/n,arcname='baseline-01/'+host+'/'+n)
    a.add(p,arcname='baseline-01/'+host+'/summary.json')
print(json.dumps(summary),flush=True)
