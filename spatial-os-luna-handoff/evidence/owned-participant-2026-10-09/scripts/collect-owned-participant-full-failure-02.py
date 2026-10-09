import hashlib,json,pathlib,re,socket,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=r/'local/m02-owned-participant-02/source';folder=source.parent/'runtime-sanitize'
manifest=json.loads((r/'local/owned-participant-candidate-01.json').read_text())['files'];locked=json.loads((source/'acceptance.lock.json').read_text())['files']
for names in [manifest,locked]:
    for n,h in names.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
run=json.loads((folder/'run-full.json').read_text());assert [x['name'] for x in run['results']]==['configure','build','harness','focused','core'] and [x['exit_code'] for x in run['results']]==[0,0,0,0,8]
log=(folder/'core.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
assert len(counts)==66 and sum(counts)==1000
errors=sum(int(n) for n in re.findall(r'^\d+: FAILED \(errors=(\d+)\)',log,re.M))
assert errors==(1 if host=='sb16' else 2)
prefixes=['WorldBootstrapTests-test_challenge_and_failed_proof_do_not_materialize_grants-'] if host=='sb16' else ['WorkspaceAuthorityTests-test_grants_do_not_make_workspace_available_implicitly-','WorldAuthorityTests-test_grant_cannot_cross_world_identity-']
summary=dict(host=host,source_files=len(manifest),locked_files=len(locked),profile='runtime-sanitize',core_checks=1339,core_failed=errors,core_passed=1339-errors,core_skipped=0,core_suites=87,harness_checks=51,P10='unrun after core failure',interpretation='World startup transport failures. Preserve them separately from later diagnostics. No overall sanitizer pass.')
p=source.parent/'summary-full-failure.json';p.write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(r/('local/owned-participant-full-failure-'+host+'-02.tar.gz'),'w:gz') as a:
    for n in ['run-full.json','configure.log','build.log','harness.log','focused.log','core.log']:
        a.add(folder/n,arcname='full-candidate-01/'+host+'/runtime-sanitize-failure/'+n)
    a.add(folder/'build/Testing/Temporary/LastTest.log',arcname='full-candidate-01/'+host+'/runtime-sanitize-failure/LastTest.log')
    for path in (folder/'server').glob('*.log'):
        if any(path.name.startswith(prefix) for prefix in prefixes):a.add(path,arcname='full-candidate-01/'+host+'/runtime-sanitize-failure/server/'+path.name)
    a.add(p,arcname='full-candidate-01/'+host+'/summary-failure.json')
print(json.dumps(summary),flush=True)
