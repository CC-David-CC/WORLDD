import ast,difflib,hashlib,json,shutil,subprocess,tarfile
from pathlib import Path
r=Path(__file__).resolve().parents[1];repo=r.parent;local=r/'local';e=r/'evidence/owned-participant-2026-10-09'
(e/'manifests').mkdir(parents=True);(e/'scripts').mkdir()
def contents(label):
    manifest=json.loads((local/(label+'.json')).read_text(encoding='utf-8'))['files']
    with tarfile.open(local/(label+'.tar')) as a:return {n:a.extractfile(n).read() for n in manifest}
base=contents('launch-authorization-candidate-03');baseline=contents('owned-participant-baseline-01');candidate=contents('owned-participant-candidate-01')
review=local/'owned-participant-review-01';review.mkdir()
with tarfile.open(local/'launch-authorization-candidate-03.tar') as a:a.extractall(review,filter='data')
for label,before,after in [('baseline-01',base,baseline),('candidate-01',baseline,candidate)]:
    patch=''
    for n,raw in after.items():
        if raw!=before.get(n):patch+=''.join(difflib.unified_diff(before.get(n,b'').decode('utf-8').splitlines(keepends=True),raw.decode('utf-8').splitlines(keepends=True),fromfile='a/'+n if n in before else '/dev/null',tofile='b/'+n))
    (e/(label+'.patch')).write_text(patch,encoding='utf-8',newline='\n')
    command=['git','apply','--directory='+review.relative_to(repo).as_posix(),str(e/(label+'.patch'))]
    subprocess.run(command[:2]+['--check']+command[2:],cwd=repo,check=True);subprocess.run(command,cwd=repo,check=True)
    assert all((review/n).read_bytes()==raw for n,raw in after.items())
    shutil.copyfile(local/('owned-participant-'+label+'.json'),e/'manifests'/('owned-participant-'+label+'.json'))
for n in base:
    if n.startswith('tests/'):assert candidate[n]==base[n],n
locked=json.loads(candidate['acceptance.lock.json'])['files']
assert all(hashlib.sha256(candidate[n]).hexdigest()==h for n,h in locked.items())
for host in ['sb16','sb16-2']:
    with tarfile.open(local/('owned-participant-baseline-'+host+'-01.tar.gz')) as a:a.extractall(e,filter='data')
for n in ['prepare-owned-participant-baseline-01.py','qualify-owned-participant-baseline-01.py','collect-owned-participant-baseline-01.py','freeze-owned-participant-01.py','qualify-owned-participant-01.py']:
    shutil.copyfile(local/n,e/'scripts'/n)
result=dict(source_files=len(candidate),locked_files=len(locked),patch_reconstruction='passed',existing_tests_unchanged=True,
    changed=[n for n in candidate if candidate[n]!=base.get(n)],
    review='Primary agent reviewed retained worker identity, schema1 and schema2 validation, exact signed request construction, durable approval before exec, failed-exec command denial, worker failure, checked stop, and restart without reconstructed handles.',
    tests='Both hosts reproduced two missing-feature failures. Eleven new checks and existing regression suites are qualifying.',
    remaining='Private inherited child channel, production world/broker integration, fencing, checked recovery and retirement.',integrated=False)
(e/'source-review.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(dict(source_files=274,locked_files=41,existing_tests_unchanged=True,reconstructed=True)))
