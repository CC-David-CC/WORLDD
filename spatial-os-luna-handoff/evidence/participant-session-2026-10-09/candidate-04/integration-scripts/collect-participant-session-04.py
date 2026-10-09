import hashlib,json,pathlib,re,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
old=json.loads((root/'local/participant-session-candidate-03.json').read_text());new=json.loads((root/'local/participant-session-candidate-04.json').read_text())
source=root/'local/m02-participant-session-source-03';changed=[n for n in old['files'] if old['files'][n]!=new['files'][n]];assert changed==['tests/core/test_workspace_authority.py']
for name,digest in old['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
fixture=root/'local/workspace-relay-fix-validation-01';receipt=json.loads((fixture/'run.json').read_text());assert receipt['host']==host and [r['exit_code'] for r in receipt['results']]==[1,0,0]
assert hashlib.sha256((root/'local/test_workspace_relay_fixed.py').read_bytes()).hexdigest()==new['files'][changed[0]]
assert 'AssertionError: False is not true' in (fixture/'runtime-sanitize-baseline.log').read_text()
files=[];summary={}
for label in ('debug','runtime-sanitize'):
    folder=root/('local/m02-participant-session-'+label+'-03');build=folder/'build';run=json.loads((folder/'run.json').read_text())
    expected_failure=host=='sb16-2' and label=='runtime-sanitize'
    if expected_failure:
        assert [r['name'] for r in run['results']]==['configure','build','harness','sessions','core']
        assert [r['exit_code'] for r in run['results']]==[0,0,0,0,8]
        p10folder=root/'local/m02-participant-session-runtime-sanitize-p10-completion-03';p10run=json.loads((p10folder/'run.json').read_text());assert p10run['exit_code']==0
        assert p10run['source_manifest_sha256']==hashlib.sha256((root/'local/participant-session-candidate-03.json').read_bytes()).hexdigest()
    else:
        assert [r['name'] for r in run['results']]==['configure','build','harness','sessions','core','P10'] and all(r['exit_code']==0 for r in run['results'])
        p10folder=folder
    names=[];partitions={}
    for part,suites,checks in [('sessions',2,32),('core',64,1120)]:
        log=(folder/(part+'.log')).read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)]
        executed=re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M);assert len(counts)==len(executed)==len(set(executed))==suites and sum(counts)==checks
        assert 'skipped=' not in log and '... skipped ' not in log
        if expected_failure and part=='core':
            assert '98% tests passed, 1 tests failed out of 64' in log
            failures=re.findall(r'^\d+/\d+ Test\s+#\d+: (\S+) .*?Failed',log,re.M);assert failures==['workspace-authority'],failures
            assert 'FAIL: test_retry_cannot_acknowledge_a_process_revoked_during_status_refresh' in log
            assert 'AssertionError: False is not true' in log
            assert len(re.findall(r'^\d+: FAIL:',log,re.M))==1 and not re.search(r'^\d+: ERROR:',log,re.M)
        else:assert f'100% tests passed, 0 tests failed out of {suites}' in log
        names+=executed;partitions[part]=dict(zip(executed,counts))
    assert len(names)==len(set(names))==66
    listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
    assert {t['name'] for t in listed['tests']}==set(names)
    assert partitions['core']['workspace-authority']==19
    fixed=(fixture/(label+'-fixed.log')).read_text();assert re.search(r'Ran 20 tests in ',fixed) and fixed.rstrip().endswith('OK') and 'skipped' not in fixed
    item=next(r for r in receipt['results'] if r['name']==label+'-fixed');assert item['test_sha256']==new['files'][changed[0]]
    for binary,digest in item['binaries'].items():assert hashlib.sha256((build/binary).read_bytes()).hexdigest()==digest,binary
    partitions['core']['workspace-authority']=20
    p10=json.loads((p10folder/'P10.json').read_text());assert p10['status']=='passed' and p10['selected']==p10['executed']==len(p10['tests'])==151 and all(t['status']=='passed' for t in p10['tests'])
    harness=(folder/'harness.log').read_text();assert re.search(r'Ran 51 tests in ',harness) and harness.rstrip().endswith('OK') and 'skipped' not in harness
    summary[label]=dict(core_checks=sum(sum(p.values()) for p in partitions.values()),core_suites=len(names),harness_checks=51,P10_cases=151,failed_final=0,skipped_final=0,original_core_failed_suites=1 if expected_failure else 0,partitions=partitions,fixture_replacement='19 original cases replaced by20 reviewed cases with all original assertions preserved',binaries={name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in ['identityd','workspaced','worldd','spatialwm','spatial-test-host','spatial-issuer-client']})
    assert summary[label]['core_checks']==1153
    prefix='candidate-04/'+host+'/'+label+'/'
    for name in ['configure.log','build.log','harness.log','sessions.log','core.log','run.json']:files.append((folder/name,prefix+'original-'+name))
    for name in ['P10.log','P10.json']:files.append((p10folder/name,prefix+name))
    if expected_failure:files.append((p10folder/'run.json',prefix+'P10-completion-run.json'))
    files.append((fixture/(label+'-fixed.log'),prefix+'workspace-authority-corrected.log'))
audit=json.loads((root/'local/m02-participant-session-audit-03/run.json').read_text());assert audit['status']=='passed' and audit['locked_files']==41 and audit['unchanged_existing_test_files']==89
summary.update(host=host,source_files=len(new['files']),scope='complete matrix with explicit fixture replacement',fixture_changed_files=changed,preserved_original_failures=True,locked_files=41)
summarypath=root/'local/participant-session-summary-final-04.json';summarypath.write_text(json.dumps(summary,indent=2)+'\n')
files += [(summarypath,'candidate-04/'+host+'/summary-final.json'),(root/'local/participant-session-candidate-04.json','candidate-04/'+host+'/source-manifest.json'),(root/'local/participant-session-candidate-03.json','candidate-04/'+host+'/original-source-manifest.json'),(root/'local/m02-participant-session-audit-03/run.json','candidate-04/'+host+'/original-schema-audit.json'),(fixture/'run.json','candidate-04/'+host+'/fixture-run.json')]
with tarfile.open(root/('local/participant-session-final-'+host+'-04.tar.gz'),'w:gz') as archive:
    for path,name in files:archive.add(path,arcname=name)
print(host,'checked composite1153core/66suites,51harness,151P10 perprofile; retained originalfailure',flush=True)
