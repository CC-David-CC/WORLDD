import hashlib,json,re,socket,xml.etree.ElementTree as ET
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff');out=r/'local/m06-presentation-transport-03'
record=json.loads((out/'run.json').read_text());assert record['status']=='commands_passed_needs_review' and record['host']==socket.gethostname()
assert all(c['exit_code']==0 for c in record['commands'])
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
manifest=json.loads((r/'local/m06-presentation-input-03/audit.json').read_text())['reviewed_source_sha256']
manifest['tests/native/test_presentation_bindings.py']=json.loads((r/'local/m06-presentation-legacy-01/audit.json').read_text())['source_sha256']
manifest.update(record['source_sha256']);assert all(sha(r/n)==v for n,v in manifest.items())
locked=json.loads((r/'acceptance.lock.json').read_text())['files'];assert all(sha(r/n)==v for n,v in locked.items())
audit=dict(host=record['host'],status='passed_focused_artifact_review',reviewed_source_sha256=manifest,profiles={},phase_complete=False,scope='PresentationStream only; no compositor transport integration claim')
for profile in ('Debug','ASan-UBSan'):
 d=out/profile;cases=ET.parse(d/'results.xml').getroot().findall('testcase')
 assert len(cases)==1 and cases[0].attrib['name']=='presentation-stream' and cases[0].attrib['status']=='run'
 assert not any(cases[0].find(k) is not None for k in ('error','failure','skipped'))
 text=(out/(profile+'-focused.log')).read_text();assert re.search(r'Ran 11 tests in ',text) and re.search(r'^\d+: OK\s*$',text,re.M)
 assert 'FAILED' not in text and 'skipped=' not in text
 logs=list(d.rglob('*.log'))+list(out.glob(profile+'-*.log'))
 for p in logs:assert not re.search(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|SUMMARY: .*Sanitizer|runtime error:|AddressSanitizer:DEADLYSIGNAL',p.read_text(errors='replace')),str(p)
 binary=r/'local/m06-build'/profile/'spatial-presentation-stream-probe'
 audit['profiles'][profile]=dict(checks=11,failed=0,skipped=0,logs_scanned=len(logs),binary=dict(path=str(binary),sha256=sha(binary)),artifacts={str(p.relative_to(out)):sha(p) for p in [*d.rglob('*'),*out.glob(profile+'-*.log')] if p.is_file()})
(out/'audit.json').write_text(json.dumps(audit,indent=2)+'\n');print(socket.gethostname(),'passed',len(manifest),'source files; 11 checks/profile')
