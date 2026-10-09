import ast,hashlib,json,tarfile
from pathlib import Path
r=Path(__file__).parent;source=r/'owned-participant-draft-01'
baseline=json.loads((r/'owned-participant-baseline-01.json').read_text(encoding='utf-8'))['files']
files={n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in baseline}
locked=json.loads((source/'acceptance.lock.json').read_text())['files'];assert all(files[n]==h for n,h in locked.items())
with tarfile.open(r/'owned-participant-baseline-01.tar') as a:
    name='tests/native/test_authority_owned_participant.py'
    old=ast.parse(a.extractfile(name).read().decode('utf-8'));new=ast.parse((source/name).read_text(encoding='utf-8'))
    tests=lambda tree:{n.name:ast.dump(n) for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
    before=tests(old);after=tests(new);assert all(after[k]==v for k,v in before.items())
    for n in baseline:
        if n.startswith('tests/') and n!=name:assert baseline[n]==files[n],n
target=r/'owned-participant-candidate-01';assert not target.with_suffix('.tar').exists()
target.with_suffix('.json').write_text(json.dumps(dict(files=files,base='owned-participant-baseline-01',new_checks=len(after)),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(target.with_suffix('.tar'),'w') as a:
    for n in files:a.add(source/n,arcname=n,recursive=False)
s=(r/'qualify-launch-authorization-03.py').read_text(encoding='utf-8')
s=s.replace('m02-launch-authorization-03','m02-owned-participant-01').replace('launch-authorization-candidate-03','owned-participant-candidate-01')
s=s.replace("pattern='", "pattern='",1).replace("|issuer-client)$'","|issuer-client|authority-(owned-participant|launch-coordinator|prepared-launch|process-scope|native-scope))$'")
s=s.replace("audit()\nbindings=", "audit()\nserver=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']\nassert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']\nbindings=")
s=s.replace("env=dict(os.environ,", "env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(target/'server'),")
s=s.replace("'spatial-key',*bindings", "'spatial-key','spatial-authority-launch-probe','spatial-security-auth','spatial-process-scope-probe','spatial-native-scope-probe',*bindings")
s=s.replace('launch authorization focused qualification complete','owned participant focused qualification complete')
(r/'qualify-owned-participant-01.py').write_text(s,encoding='utf-8',newline='\n')
print(json.dumps(dict(source_files=len(files),locked_files=len(locked),new_checks=len(after),existing_tests_unchanged=True)))
