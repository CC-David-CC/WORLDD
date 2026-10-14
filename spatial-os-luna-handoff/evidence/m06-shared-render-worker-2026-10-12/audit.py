import hashlib,json,re,socket,xml.etree.ElementTree as ET
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff');out=r/'local/m06-shared-render-worker-05'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
manifest=json.loads((r/'local/m06-presentation-server-06/audit.json').read_text())['reviewed_source_sha256']
manifest.update(json.loads((r/'local/m06-shared-worker-source.json').read_text()))
assert all(sha(r/n)==v for n,v in manifest.items())
locked=json.loads((r/'acceptance.lock.json').read_text())['files'];assert all(sha(r/n)==v for n,v in locked.items())
audit=dict(host=socket.gethostname(),status='focused_checks_passed',reviewed_source_sha256=manifest,profiles={},phase_complete=False)
for profile in ('Debug','ASan-UBSan'):
 logs=[]
 for run,expected,counts in [('05',{'shared-render-client','presentation-server','render-runtime','render-observation-client','native-world_surface_groups'},[4,9,14,15,18]),('06',{'presentation-input-bindings','native-compositor_identity'},[7,10])]:
  directory=r/('local/m06-shared-render-worker-'+run)
  record=json.loads((directory/'run.json').read_text());assert record['status']=='passed_needs_review'
  assert all(c['exit_code']==0 for c in record['commands'])
  cases=ET.parse(directory/(profile+'-results.xml')).getroot().findall('testcase')
  assert {c.attrib['name'] for c in cases}==expected
  assert all(c.attrib['status']=='run' and not any(c.find(k) is not None for k in ('error','failure','skipped')) for c in cases)
  text=(directory/(profile+'-tests.log')).read_text()
  assert sorted(map(int,re.findall(r'Ran (\d+) tests in ',text)))==counts
  assert len(re.findall(r'^\d+: OK\s*$',text,re.M))==len(counts)
  assert 'FAILED' not in text and 'skipped=' not in text
  logs+=list((directory/profile).rglob('*.log'))+list(directory.glob(profile+'-*.log'))
 for p in logs:assert not re.search(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|SUMMARY: .*Sanitizer|runtime error:|AddressSanitizer:DEADLYSIGNAL',p.read_text(errors='replace')),str(p)
 audit['profiles'][profile]=dict(checks=77,failures=0,skips=0,logs_scanned=len(logs),binary_sha256={name:sha(r/'local/m06-build'/profile/name) for name in ('worldd','spatialwm','spatial-test-host')})
(out/'audit.json').write_text(json.dumps(audit,indent=2)+'\n');print(socket.gethostname(),'passed',len(manifest),'source files; 77 checks/profile')
