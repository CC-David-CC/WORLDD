import hashlib,json,re,socket,xml.etree.ElementTree as ET
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff')
out=r/'local/m06-presentation-legacy-01';record=json.loads((out/'run.json').read_text());prior=json.loads((r/'local/m06-presentation-input-03/audit.json').read_text())
assert record['status']=='passed_commands_needs_review' and record['host']==prior['host']==socket.gethostname()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
name='tests/native/test_presentation_bindings.py'
assert sha(r/name)==record['source_sha256']
assert all(sha(r/n)==v for n,v in prior['reviewed_source_sha256'].items() if n!=name)
locked=json.loads((r/'acceptance.lock.json').read_text())['files'];assert all(sha(r/n)==v for n,v in locked.items())
result=dict(host=record['host'],status='passed_focused_artifact_review',profiles={},updated_file=name,source_sha256=record['source_sha256'],prior_audit_sha256=sha(r/'local/m06-presentation-input-03/audit.json'),phase_complete=False)
for profile in ('Debug','ASan-UBSan'):
 assert record['profiles'][profile]['exit_code']==0
 d=out/profile;cases=ET.parse(d/'results.xml').getroot().findall('testcase')
 assert len(cases)==1 and cases[0].attrib['name']=='presentation-input-bindings' and cases[0].attrib['status']=='run'
 assert not any(cases[0].find(k) is not None for k in ('error','failure','skipped'))
 text=(d/'focused.log').read_text();assert re.search(r'Ran 10 tests in ',text) and re.search(r'^\d+: OK\s*$',text,re.M)
 assert 'FAILED' not in text and 'skipped=' not in text
 logs=list(d.rglob('*.log'))
 for p in logs:assert not re.search(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|SUMMARY: .*Sanitizer|runtime error:|AddressSanitizer:DEADLYSIGNAL',p.read_text(errors='replace')),str(p)
 result['profiles'][profile]=dict(checks=10,combined_checks=prior['profiles'][profile]['checks']-9+10,failed=0,skipped=0,logs_scanned=len(logs),artifacts={str(p.relative_to(out)):sha(p) for p in d.rglob('*') if p.is_file()})
(out/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
print('passed',record['host'],'77 combined checks/profile')
