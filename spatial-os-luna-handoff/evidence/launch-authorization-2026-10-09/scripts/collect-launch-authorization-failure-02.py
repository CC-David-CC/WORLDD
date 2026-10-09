import hashlib,json,pathlib,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
out=r/'local/m02-launch-authorization-02'
manifest=json.loads((r/'local/launch-authorization-candidate-02.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((out/'source'/n).read_bytes()).hexdigest()==h,n
files=[];summary=dict(host=host,source_files=len(manifest),integrated=False)
diagnostic=r/'local/m02-client-config-overwrite-01'
run=json.loads((diagnostic/'run.json').read_text());log=(diagnostic/'diagnostic.log').read_text()
assert run['exit_code']==1 and 'Another client configuration overwrote the first launch input' in log
summary['config_overwrite_baseline']='Reproduced; original client configuration was overwritten by a second client.'
for n in ['driver.py','run.json','diagnostic.log']:files.append((diagnostic/n,'fixture-config-overwrite/'+host+'/'+n))
if host=='sb16-2':
    target=out/'runtime-sanitize';run=json.loads((target/'run.json').read_text());log=(target/'focused.log').read_text()
    assert [x['exit_code'] for x in run['results']]==[0,0,0,8]
    assert 'FAIL: test_client_unsolicited_authorization_is_rejected' in log and 'FAILED (failures=1)' in log
    summary['candidate02_runtime']='283 of 284 checks passed; unsolicited-reply fixture failed before the required request reached its peer.'
    for n in ['run.json','configure.log','build.log','harness.log','focused.log']:files.append((target/n,'candidate-02/'+host+'/runtime-sanitize/'+n))
    diagnostic=r/'local/m02-launch-authorization-unsolicited-diagnostic-01'
    run=json.loads((diagnostic/'run.json').read_text());log=(diagnostic/'diagnostic.log').read_text()
    assert run['exit_code']==1 and 'Ran 10 tests' in log and 'FAILED (failures=2)' in log
    assert 'Issuer client deadline exceeded' in log and 'Unrequested or expired launch success' in log
    summary['unsolicited_diagnostic']='Ten repetitions; two failures. One peer received an extra hello; another never received the required request.'
    for n in ['driver.py','run.json','diagnostic.log']:files.append((diagnostic/n,'fixture-unsolicited-diagnostic/'+host+'/'+n))
p=r/('local/launch-authorization-fixture-summary-'+host+'-02.json');p.write_text(json.dumps(summary,indent=2)+'\n')
files.append((p,'fixture-config-overwrite/'+host+'/summary.json'))
with tarfile.open(r/('local/launch-authorization-fixture-'+host+'-02.tar.gz'),'w:gz') as a:
    for p,n in files:a.add(p,arcname=n)
print(json.dumps(summary),flush=True)
