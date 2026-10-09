import hashlib,json,pathlib,re,subprocess,tarfile
r=pathlib.Path(__file__).resolve().parents[1];repo=r.parent;e=r/'evidence/participant-incarnation-2026-10-09'
before=json.loads((e/'manifests/authority-launch-coordinator-candidate-03.json').read_text(encoding='utf-8'))['files']
after=json.loads((e/'manifests/participant-incarnation-candidate-01.json').read_text(encoding='utf-8'))['files']
review=r/'local/participant-incarnation-review-01';assert not review.exists();review.mkdir()
with tarfile.open(r/'local/authority-launch-coordinator-candidate-03.tar') as archive:archive.extractall(review,filter='data')
command=['git','apply','--directory='+review.relative_to(repo).as_posix(),str(e/'candidate-01.patch')]
subprocess.run(command[:2]+['--check']+command[2:],cwd=repo,check=True);subprocess.run(command,cwd=repo,check=True)
for name,digest in after.items():assert hashlib.sha256((review/name).read_bytes()).hexdigest()==digest,name
for name,digest in before.items():
    if name.startswith('tests/'):assert after[name]==digest,name
for host in ['sb16','sb16-2']:
    summary=json.loads((e/f'candidate-01/{host}/summary-final.json').read_text(encoding='utf-8'))
    for profile in ['debug','runtime-sanitize']:
        result=summary[profile];assert tuple(result[n] for n in ['checks','new_checks','existing_checks','suites','harness_checks','failed','skipped'])==(84,5,79,6,51,0,0)
        text=(e/f'candidate-01/{host}/{profile}/focused.log').read_text(encoding='utf-8')
        counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
        assert len(counts)==6 and sum(counts)==84 and '100% tests passed, 0 tests failed out of 6' in text
    assert summary['schema']['explicit_presence'] and summary['schema']['only_added_field']=='AuthorityParticipantRuntimeConfig.service_incarnation=6'
    full=r/f'evidence/authority-scopes-combined-2026-10-09/candidate-01/{host}'
    result=json.loads((full/'summary-final.json').read_text(encoding='utf-8'))
    for profile in ['debug','runtime-sanitize']:
        assert tuple(result[profile][n] for n in ['core_checks','core_suites','harness_checks','P10_cases','failed','skipped'])==(1260,81,51,151,0,0)
        for part,suites,total in [('focused',2,21),('core',79,1239)]:
            text=(full/profile/(part+'.log')).read_text(encoding='utf-8');counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
            assert len(counts)==suites and sum(counts)==total and f'100% tests passed, 0 tests failed out of {suites}' in text
        report=json.loads((full/profile/'P10.json').read_text(encoding='utf-8'))
        assert report['status']=='passed' and report['executed']==len(report['tests'])==151 and all(t['status']=='passed' for t in report['tests'])
print(json.dumps(dict(reconstructed_files=271,existing_tests_unchanged=True,focused_both_profiles_both_hosts='passed',
    combined_scopes_full_both_profiles_both_hosts='passed',full_incarnation='pending')))
