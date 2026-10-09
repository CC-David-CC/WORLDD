import ast,hashlib,json,tarfile
from pathlib import Path
r=Path(__file__).parent;source=r/'participant-relay-draft-01'
base=json.loads((r/'participant-channel-candidate-02.json').read_text())['files']
files={n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in sorted([*base,'tests/native/test_authority_participant_relay.py'])}
for n,h in base.items():
    if n.startswith('tests/'):assert files[n]==h,n
for n,h in json.loads((source/'acceptance.lock.json').read_text())['files'].items():assert files[n]==h,n
tree=ast.parse((source/'tests/native/test_authority_participant_relay.py').read_text())
tests=[n.name for c in tree.body if isinstance(c,ast.ClassDef) and c.name=='ParticipantRelayTests' for n in c.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
assert len(tests)==10,tests
target=r/'participant-relay-candidate-01';assert not target.with_suffix('.json').exists()
target.with_suffix('.json').write_text(json.dumps(dict(files=files,base='participant-channel-candidate-02',tests=tests,changed=[n for n in files if files[n]!=base.get(n)]),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(target.with_suffix('.tar'),'w') as a:
    for n in files:a.add(source/n,arcname=n,recursive=False)
s=(r/'qualify-participant-channel-02.py').read_text().replace('m02-participant-channel-02','m02-participant-relay-01').replace('participant-channel-candidate-02','participant-relay-candidate-01').replace('authority-(owned-participant','authority-(participant-relay|owned-participant')
s=s.replace('private participant channel focused qualification complete','supervisor participant relay focused qualification complete')
(r/'qualify-participant-relay-01.py').write_text(s,encoding='utf-8',newline='\n')
print(json.dumps(dict(files=len(files),checks=len(tests),changed=[n for n in files if files[n]!=base.get(n)])))
