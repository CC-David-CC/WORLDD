import ast,hashlib,json,tarfile
from pathlib import Path
r=Path(__file__).parent;source=r/'participant-channel-draft-01'
base=json.loads((r/'owned-participant-candidate-01.json').read_text())['files']
added=['src/identity/participant_stream.hpp','tests/core/test_participant_channel.py']
files={n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in sorted([*base,*added])}
changed=[n for n,h in base.items() if files[n]!=h]
assert changed==sorted(changed) or set(changed)=={'CMakeLists.txt','proto/spatial/identity/v1/issuer.proto','src/identity/participant_runtime.cpp','src/testing/participant_runtime_probe.cpp'}
assert set(changed)=={'CMakeLists.txt','proto/spatial/identity/v1/issuer.proto','src/identity/participant_runtime.cpp','src/testing/participant_runtime_probe.cpp'},changed
for n,h in json.loads((source/'acceptance.lock.json').read_text())['files'].items():assert files[n]==h,n
tree=ast.parse((source/'tests/core/test_participant_channel.py').read_text())
tests=[n.name for c in tree.body if isinstance(c,ast.ClassDef) and c.name=='ParticipantChannelTests' for n in c.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
assert len(tests)==17,tests
target=r/'participant-channel-candidate-01'
assert not target.with_suffix('.json').exists() and not target.with_suffix('.tar').exists()
target.with_suffix('.json').write_text(json.dumps(dict(files=files,base='owned-participant-candidate-01',changed=changed,added=added,tests=tests),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(target.with_suffix('.tar'),'w') as a:
    for n in files:a.add(source/n,arcname=n,recursive=False)
s=(r/'qualify-owned-participant-02.py').read_text()
start=s.index('prior=json.loads(');end=s.index("out=r/'local/m02-owned-participant-02'")
s=s[:start]+s[end:]
s=s.replace('m02-owned-participant-02','m02-participant-channel-01').replace('owned-participant-candidate-01','participant-channel-candidate-01')
s=s.replace('client|incarnation|runtime|barrier','client|incarnation|channel|runtime|barrier')
s=s.replace('owned participant focused qualification complete','private participant channel focused qualification complete')
(r/'qualify-participant-channel-01.py').write_text(s,encoding='utf-8',newline='\n')
print(json.dumps(dict(files=len(files),changed=changed,new=added,checks=len(tests))))
