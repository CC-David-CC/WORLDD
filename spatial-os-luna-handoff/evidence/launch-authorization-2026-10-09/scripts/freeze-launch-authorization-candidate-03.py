import ast, difflib, hashlib, json, shutil, subprocess, tarfile
from pathlib import Path
r = Path(__file__).parent
source = r/'launch-authorization-draft-01'
base = json.loads((r/'launch-authorization-candidate-02.json').read_text(encoding='utf-8'))['files']
files = {n: hashlib.sha256((source/n).read_bytes()).hexdigest() for n in base}
changed = sorted(n for n in files if files[n] != base[n])
assert changed == ['tests/core/test_issuer_client.py', 'tests/core/test_launch_authorization.py'], changed
with tarfile.open(r/'launch-authorization-candidate-02.tar') as a:
    before = {n: a.extractfile(n).read() for n in base}
for n in changed:
    old = ast.parse(before[n].decode('utf-8'))
    new = ast.parse((source/n).read_text(encoding='utf-8'))
    oldtests = {node.name: ast.dump(node) for node in ast.walk(old) if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')}
    newtests = {node.name: ast.dump(node) for node in ast.walk(new) if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')}
    assert all(newtests[k] == v for k,v in oldtests.items()), n
locked = json.loads((source/'acceptance.lock.json').read_text())['files']
assert all(files[n] == h for n,h in locked.items())
target = r/'launch-authorization-candidate-03'
assert not target.with_suffix('.tar').exists()
target.with_suffix('.json').write_text(json.dumps(dict(files=files,base='launch-authorization-candidate-02',changed=changed),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(target.with_suffix('.tar'),'w') as a:
    for n in files: a.add(source/n,arcname=n,recursive=False)
s = (r/'qualify-launch-authorization-02.py').read_text(encoding='utf-8').replace('launch-authorization-02','launch-authorization-03').replace('launch-authorization-candidate-02','launch-authorization-candidate-03')
(r/'qualify-launch-authorization-03.py').write_text(s,encoding='utf-8',newline='\n')
s = (r/'collect-launch-authorization-02.py').read_text(encoding='utf-8')
start = s.index("prior=r/'local/m02-launch-authorization-01'")
end = s.index('summary=dict(',start)
s = s[:start]+"files=[]\n"+s[end:]
s = s.replace('launch-authorization-02','launch-authorization-03').replace('launch-authorization-candidate-02','launch-authorization-candidate-03')
s = s.replace('candidate=2','candidate=3').replace('candidate-02/','candidate-03/').replace("+'-02.","+'-03.")
s = s.replace("'identity-launch-authorization':22","'identity-launch-authorization':23").replace('new_checks=22','new_checks=23').replace('sum(counts)-22','sum(counts)-23').replace('284 focused','285 focused')
(r/'collect-launch-authorization-03.py').write_text(s,encoding='utf-8',newline='\n')
e = r.parent/'evidence/launch-authorization-2026-10-09'
patch = ''.join(''.join(difflib.unified_diff(before[n].decode('utf-8').splitlines(keepends=True),(source/n).read_text(encoding='utf-8').splitlines(keepends=True),fromfile='a/'+n,tofile='b/'+n)) for n in changed)
(e/'candidate-03.patch').write_text(patch,encoding='utf-8',newline='\n')
review = r/'launch-authorization-review-03'; review.mkdir()
with tarfile.open(r/'launch-authorization-candidate-02.tar') as a: a.extractall(review,filter='data')
repo = r.parent.parent
command = ['git','apply','--directory='+review.relative_to(repo).as_posix(),str((e/'candidate-03.patch').resolve())]
subprocess.run(command[:2]+['--check']+command[2:],cwd=repo,check=True)
subprocess.run(command,cwd=repo,check=True)
assert all(hashlib.sha256((review/n).read_bytes()).hexdigest()==h for n,h in files.items())
shutil.copyfile(target.with_suffix('.json'),e/'manifests'/target.with_suffix('.json').name)
for name in ['freeze-launch-authorization-candidate-03.py','qualify-launch-authorization-03.py','collect-launch-authorization-03.py','diagnose-client-config-overwrite-01.py','diagnose-launch-authorization-unsolicited-01.py']:
    shutil.copyfile(r/name,e/'scripts'/name)
(e/'candidate-03-review.json').write_text(json.dumps(dict(changed=changed,source_files=len(files),locked_files=len(locked),production_unchanged=True,existing_test_bodies_unchanged=True,patch_reconstruction='passed',review='Each fixture client gets a closed, private, unique configuration snapshot before process launch. Prior rejection assertions and deadlines remain unchanged. A new regression checks independent contents and permissions.',qualification='pending',integrated=False),indent=2)+'\n',encoding='utf-8',newline='\n')
print('Frozen 273 files; reconstructed patch; 41 locks and all existing test bodies unchanged; production unchanged.')
