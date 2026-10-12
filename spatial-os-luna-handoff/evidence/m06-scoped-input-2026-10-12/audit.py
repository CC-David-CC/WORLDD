import hashlib
import json
from pathlib import Path
import re
import socket
import xml.etree.ElementTree as ET
root = Path('/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff')
old = root/'local/m06-scoped-input-04'
out = root/'local/m06-scoped-input-05'
prior=json.loads((old/'run.json').read_text())
record=json.loads((out/'run.json').read_text())
assert record['status']=='commands_passed_needs_review'
assert prior['status']=='failed'
assert record['host']==prior['host']==socket.gethostname()
assert all(c['exit_code']==0 for c in record['commands'])
assert [c['name'] for c in prior['commands'] if c['exit_code']] == ['Debug-focused']
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert all(sha(root/n)==v for n,v in record['source_sha256'].items())
reviewed=json.loads((root/'local/m06-reviewed-source.json').read_text())
assert all(sha(root/n)==v for n,v in reviewed.items())
changed=[n for n,v in prior['source_sha256'].items() if record['source_sha256'][n]!=v]
assert changed==['tests/core/test_issuer_client_defaults.py'],changed
locked=json.loads((root/'acceptance.lock.json').read_text())['files']
assert all(sha(root/n)==v for n,v in locked.items())
expected=set(record['selected']); assert len(expected)==29
assert set(prior['selected'])==expected
markers=re.compile(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|SUMMARY: .*Sanitizer|runtime error:|AddressSanitizer:DEADLYSIGNAL')
audit=dict(host=record['host'], source_sha256=record['source_sha256'], locked_files=len(locked), reviewed_source_files=len(reviewed), profiles={}, phase_complete=False,
    scope='Scoped native input and normal issuer fixture budgets. No world-transition qualification.',
    preserved_failure='run04 issuer-client-defaults had an uninitialized fixture port; corrected explicitly without changing assertions')
for profile in ('Debug','ASan-UBSan'):
    runs=[old,out] if profile=='Debug' else [out]
    counts={}; origins={}; artifacts={}; logs_scanned=0; binaries={}; scopes=0
    for run in runs:
        directory=run/profile
        suite=ET.parse(directory/'results.xml').getroot()
        cases=suite.findall('testcase')
        requested=expected if run==old or profile!='Debug' else {'issuer-client-defaults'}
        assert {c.attrib['name'] for c in cases}==requested
        failed=[c.attrib['name'] for c in cases if any(c.find(k) is not None for k in ('failure','error','skipped'))]
        assert failed==(['issuer-client-defaults'] if run==old else []),failed
        verbose=(run/(profile+'-focused.log')).read_text()
        for case in cases:
            name=case.attrib['name']
            if run==old and name=='issuer-client-defaults':continue
            assert case.attrib['status']=='run'
            starts=re.findall(r'^\s*Start\s+(\d+): '+re.escape(name)+r'\s*$',verbose,re.M)
            assert len(starts)==1,(name,starts)
            prefix=starts[0]+': '
            output='\n'.join(line[len(prefix):] for line in verbose.splitlines() if line.startswith(prefix))
            matches=re.findall(r'Ran (\d+) tests? in ',output)
            assert len(matches)==1,(name,matches)
            assert re.search(r'^OK\s*$',output,re.M),(name,output[-300:])
            assert not re.search(r'\bskipped=|\bFAILED\b',output)
            counts[name]=int(matches[0]);origins[name]=str(run.relative_to(root))
        server=json.loads((directory/'server-build/run.json').read_text())
        assert server['status']=='passed' and server['sanitizers']==(profile=='ASan-UBSan')
        assert all(c['exit_code']==0 for c in server['commands'])
        for name, binary in server['binaries'].items():
            assert sha(Path(binary['path']))==binary['sha256']
            binaries[name]=binary
        logs=sorted(directory.rglob('*.log'))+sorted(run.glob(profile+'-*.log'))
        assert logs
        for path in logs:
            assert not markers.search(path.read_text(errors='replace')),str(path)
        logs_scanned+=len(logs)
        for path in (directory/'scopes').glob('*.json'):
            receipt=json.loads(path.read_text())
            if 'scope_removed' in receipt:
                assert receipt['scope_removed'] and not receipt.get('unexpected_population',False),str(path)
                scopes+=1
        for path in [*directory.rglob('*'),*run.glob(profile+'-*.log'),run/'run.json']:
            if path.is_file():artifacts[str(path.relative_to(root))]=sha(path)
    assert set(counts)==expected
    audit['profiles'][profile]=dict(suites=len(counts), checks=sum(counts.values()),counts=counts,origins=origins,
        failed=0,skipped=0,logs_scanned=logs_scanned,checked_scope_receipts=scopes,server_binaries=binaries,artifacts=artifacts)
audit['status']='passed_focused_artifact_review'
(out/'audit.json').write_text(json.dumps(audit,indent=2)+'\n')
print(json.dumps(dict(host=audit['host'],status=audit['status'],profiles={p:{k:v for k,v in row.items() if k not in ('counts','origins','artifacts','server_binaries')} for p,row in audit['profiles'].items()}),indent=2))
