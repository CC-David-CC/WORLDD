from pathlib import Path
r=Path(__file__).parent
s=(r/'qualify-launch-authorization-full-03.py').read_text(encoding='utf-8')
start=s.index("source=r/'local/m02-launch-authorization-full-03/source'")
end=s.index('manifest=',start)
s=s[:start]+"source=r/'local/m02-owned-participant-02/source'\n"+s[end:]
s=s.replace('launch-authorization-candidate-03','owned-participant-candidate-01')
s=s.replace("out=source.parent/profile;out.mkdir();build=out/'build'", "out=source.parent/profile;build=out/'build'\n    prior=json.loads((out/'run.json').read_text())\n    assert [x['name'] for x in prior['results']]==['configure','build','harness','focused'] and all(x['exit_code']==0 for x in prior['results'])\n    assert not (out/'run-full.json').exists() and not (out/'core.log').exists()")
first=s.index('    commands=');last=s.index('    for name,command in commands:',first)
pattern="^(identity-(launch-authorization|supervisor-registry|keys|issuer|enrollment|bootstrap|revocation|participants|participant-(client|incarnation|runtime|barrier|barrier-client|session|session-client))|issuer-client|authority-(owned-participant|launch-coordinator|prepared-launch|process-scope|native-scope))$"
s=s[:first]+"    commands=[('core',['ctest','--test-dir',str(build),'-V','-E',"+repr(pattern)+"]),('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]\n    results=list(prior['results'])\n"+s[last:]
s=s.replace("(out/'run.json').write_text", "(out/'run-full.json').write_text").replace('full launch authorization qualification complete','full owned participant qualification complete')
# Both focused profiles must complete before this continuation starts.
point=s.index('for profile,sanitize in')
s=s[:point]+"for profile in ['debug','runtime-sanitize']:\n    run=json.loads((source.parent/profile/'run.json').read_text())\n    assert len(run['results'])==4 and all(x['exit_code']==0 for x in run['results'])\n"+s[point:]
(r/'finish-owned-participant-full-02.py').write_text(s,encoding='utf-8',newline='\n')
s=(r/'collect-launch-authorization-full-03.py').read_text(encoding='utf-8')
s=s.replace('m02-launch-authorization-full-03','m02-owned-participant-02').replace('launch-authorization-candidate-03','owned-participant-candidate-01').replace('launch-authorization-full','owned-participant-full')
s=s.replace('candidate=3,output=3','candidate=1,output=2').replace('full-candidate-03','full-candidate-01').replace("+'-03.","+'-02.")
s=s.replace("('focused',1,23)","('focused',21,339)").replace("('core',85,1305)","('core',66,1000)")
s=s.replace('len(names)==86 and total==1328','len(names)==87 and total==1339').replace('verified273','verified274')
s=s.replace("out/'run.json'","out/'run-full.json'").replace("['run.json']","['run-full.json']")
(r/'collect-owned-participant-full-02.py').write_text(s,encoding='utf-8',newline='\n')
print('Prepared cumulative gate continuation using the same fully built, frozen output02 binaries; not started.')
