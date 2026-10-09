import ast,hashlib,json,tarfile
from pathlib import Path
r=Path(__file__).parent;source=r/'participant-channel-draft-01'
prior=json.loads((r/'participant-channel-candidate-01.json').read_text())
files={n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in prior['files']}
assert [n for n,h in prior['files'].items() if files[n]!=h]==['tests/core/test_participant_channel.py']
with tarfile.open(r/'participant-channel-candidate-01.tar') as a:old=a.extractfile('tests/core/test_participant_channel.py').read().decode()
new=(source/'tests/core/test_participant_channel.py').read_text()
assert new.replace('                peer.close();parent.close();runtime.close()\n','').replace('runtime,parent,peer,auth,_,_=self.ready_private();runtime.send(55,','runtime,parent,_,auth,_,_=self.ready_private();runtime.send(55,')==old
target=r/'participant-channel-candidate-02';assert not target.with_suffix('.json').exists()
result=dict(prior,files=files,predecessor='participant-channel-candidate-01',correction='Close each completed negative subcase TLS and child connection. Keep connection limit8 and every assertion unchanged. Production files identical.')
target.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(target.with_suffix('.tar'),'w') as a:
    for n in files:a.add(source/n,arcname=n,recursive=False)
for label in ['qualify','collect']:
    script=(r/(label+'-participant-channel-01.py')).read_text().replace('m02-participant-channel-01','m02-participant-channel-02').replace('participant-channel-candidate-01','participant-channel-candidate-02')
    if label=='collect':script=script.replace('candidate=1','candidate=2').replace('candidate-01/','candidate-02/').replace("+'-01.tar.gz'","+'-02.tar.gz'")
    (r/(label+'-participant-channel-02.py')).write_text(script,encoding='utf-8',newline='\n')
print('Candidate02 frozen276files. Only new fixture lifecycle changed;17tests and all production remain identical.')
