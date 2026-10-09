from pathlib import Path
r=Path(__file__).parent
s=(r/'finish-owned-participant-full-02.py').read_text()
s=s.replace('m02-owned-participant-02','m02-participant-relay-01').replace('owned-participant-candidate-01','participant-relay-candidate-01')
s=s.replace('client|incarnation|runtime|barrier','client|incarnation|channel|runtime|barrier').replace('authority-(owned-participant','authority-(participant-relay|owned-participant')
s=s.replace('full owned participant qualification complete','full participant relay qualification complete')
(r/'finish-participant-relay-full-01.py').write_text(s,encoding='utf-8',newline='\n')
s=(r/'collect-owned-participant-full-02.py').read_text()
s=s.replace('m02-owned-participant-02','m02-participant-relay-01').replace('owned-participant-candidate-01','participant-relay-candidate-01').replace('owned-participant-full','participant-relay-full')
s=s.replace('output=2','output=1').replace("+'-02.","+'-01.").replace("('focused',21,339)","('focused',23,366)")
s=s.replace('len(names)==87 and total==1339','len(names)==89 and total==1366').replace('verified274','verified277')
(r/'collect-participant-relay-full-01.py').write_text(s,encoding='utf-8',newline='\n')
print('Prepared full continuation:366focused+1000remaining=1366core across89suites,51harness,151P10 each profile; not started.')
