import hashlib,json,pathlib,re,subprocess,tarfile
r=pathlib.Path(__file__).resolve().parents[1];repo=r.parent
e=r/'evidence/authority-launch-coordinator-2026-10-09'
base=json.loads((e/'manifests/authority-prepared-launch-candidate-01.json').read_text(encoding='utf-8'))['files']
manifests={n:json.loads((e/f'manifests/authority-launch-coordinator-candidate-{n:02}.json').read_text(encoding='utf-8'))['files'] for n in [1,2,3]}
review=r/'local/authority-launch-review-03';assert not review.exists();review.mkdir()
with tarfile.open(r/'local/authority-prepared-launch-candidate-01.tar') as archive:archive.extractall(review,filter='data')
for n,patch in [(1,'candidate-01.patch'),(2,'partial-commit-correction-02.patch'),(3,'cgroup-fixture-correction-03.patch')]:
    command=['git','apply','--directory='+review.relative_to(repo).as_posix(),str(e/patch)]
    subprocess.run(command[:2]+['--check']+command[2:],cwd=repo,check=True)
    subprocess.run(command,cwd=repo,check=True)
    for name,digest in manifests[n].items():assert hashlib.sha256((review/name).read_bytes()).hexdigest()==digest,(n,name)
assert {n for n in manifests[2] if manifests[2][n]!=manifests[3][n]}=={'tests/native/test_authority_launch.py'}
for name,digest in base.items():
    if name.startswith('tests/') or name.startswith('src/supervisor/') or name=='src/workspace/native_launch.cpp':assert manifests[3][name]==digest,name
for host in ['sb16','sb16-2']:
    summary=json.loads((e/f'candidate-01/{host}/summary.json').read_text(encoding='utf-8'))
    for profile in ['debug','runtime-sanitize']:
        result=summary[profile];assert tuple(result[k] for k in ['focused_checks','suites','harness_checks','failed','skipped'])==(28,3,51,0,0)
    diagnostic=json.loads((e/f'fixture-correction-03/{host}/summary.json').read_text(encoding='utf-8'))
    assert diagnostic['candidate02']['passed']==42 and diagnostic['candidate02']['failed']==1
    assert diagnostic['fixture03']['checks']==diagnostic['fixture03']['passed']==14 and diagnostic['fixture03']['failed']==0
    assert diagnostic['fixture03']['test_sha256']==manifests[3]['tests/native/test_authority_launch.py']
    full=r/f'evidence/m02-corrections-2026-10-09/candidate-09/{host}'
    summary=json.loads((full/'summary-final.json').read_text(encoding='utf-8'))
    for profile in ['debug','runtime-sanitize']:
        result=summary[profile];assert tuple(result[k] for k in ['core_checks','core_suites','harness_checks','P10_cases','failed','skipped'])==(1239,79,51,151,0,0)
        for part,suites,total in [('focused',13,86),('core',66,1153)]:
            text=(full/profile/(part+'.log')).read_text(encoding='utf-8')
            counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
            assert len(counts)==suites and sum(counts)==total and f'100% tests passed, 0 tests failed out of {suites}' in text
        report=json.loads((full/profile/'P10.json').read_text(encoding='utf-8'))
        assert report['status']=='passed' and report['executed']==len(report['tests'])==151 and all(t['status']=='passed' for t in report['tests'])
print(json.dumps(dict(reconstructed_candidates=[1,2,3],source_files=270,existing_tests_unchanged=True,
    scope_implementations_unchanged=True,participant09_full_both_profiles='passed',coordinator03_full='pending')))
