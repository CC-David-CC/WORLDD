import hashlib,json,pathlib,re,socket,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=root/'local/m02-participant-fixtures-05/source';out=source.parent/'debug'
manifest=json.loads((root/'local/participant-fixtures-candidate-04.json').read_text())['files']
for name,digest in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
run=json.loads((out/'run.json').read_text());assert [r['name'] for r in run['results']]==['configure','build','harness','focused','core']
assert [r['exit_code'] for r in run['results']]==[0,0,0,0,8]
core=(out/'core.log').read_text();focused=(out/'focused.log').read_text()
assert '99% tests passed, 1 tests failed out of 67' in core
assert 'FAIL: test_parent_revocation_prevents_reconnect_from_restoring_access' in core
counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',core+focused,re.M)]
assert len(counts)==75 and sum(counts)==1227
assert '... skipped' not in core+focused and 'skipped=' not in core+focused
assert len(re.findall(r'^\d+: FAIL: ',core,re.M))==1 and not re.search(r'^\d+: ERROR: ',core,re.M)
summary=dict(host=host,candidate=4,output=5,profile='Debug',source_files=len(manifest),
    core_checks=1227,core_suites=75,passed=1226,failed=1,skipped=0,harness_checks=51,
    failed_test='WorldBootstrapTests.test_parent_revocation_prevents_reconnect_from_restoring_access',
    P10='unrun after core failure',runtime_sanitize='unrun after Debug core failure',integrated=False)
receipt=root/('local/participant-failure-04-'+host+'.json');receipt.write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(root/('local/participant-failure-04-'+host+'.tar.gz'),'w:gz') as archive:
    prefix='candidate-04/'+host
    archive.add(receipt,arcname=prefix+'/summary-failure.json')
    archive.add(root/'local/participant-fixtures-candidate-04.json',arcname=prefix+'/source-manifest.json')
    for name in ['run.json','configure.log','build.log','harness.log','focused.log','core.log']:
        archive.add(out/name,arcname=prefix+'/debug/'+name)
    for path in (out/'server').glob('*.log'):
        archive.add(path,arcname=prefix+'/debug/service-logs/'+path.name)
print(json.dumps(summary))
