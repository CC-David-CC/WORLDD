"""Verify focused results and export byte-identical diagnostic evidence."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0]=='sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m02-issuer-timeout-02'
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
manifest=json.loads((root/'manifest.json').read_bytes())
assert len(manifest['files'])==351 and len(manifest['locked_files'])==41
assert all(sha(root/'source'/name)==digest for name,digest in manifest['files'].items())
assert all(manifest['files'][name]==digest for name,digest in manifest['locked_files'].items())
attachments={}
def add(path):
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base)
    assert path.suffix in ('.json','.log','.pb','.frame','.txt','.py','.cpp','.bt')
    data=path.read_bytes()
    assert b'-----BEGIN '+b'PRIVATE KEY-----' not in data
    assert not re.search(rb'Bearer '+rb'fleet-[A-Za-z0-9_-]{12,}',data)
    attachments[path.relative_to(base).as_posix()]=sha(path)
profiles={}
for profile in ('debug','runtime-sanitize'):
    out=root/'checks'/profile;build=root/profile/'build'
    record=json.loads((out/'review.json').read_bytes())
    assert record['status']=='passed' and set(record['suites'])==set(manifest['selected_suites'])
    assert record['source']==manifest['files']
    assert all(sha(build/name)==digest for name,digest in record['binaries'].items())
    assert sha(Path(record['server']['path']))==record['server']['sha256']
    assert 'SPATIAL_SANITIZERS:BOOL='+('ON' if profile=='runtime-sanitize' else 'OFF') in (build/'CMakeCache.txt').read_text()
    for command in record['commands']:
        assert command['exit_code']==0 and sha(out/(command['label']+'.log'))==command['sha256']
    for suite,count in record['suites'].items():
        log=(out/(suite+'.log')).read_text()
        assert re.findall(r'Ran (\d+) tests',log)==[str(count)]
        assert re.search(r'\d+: OK\s*\n',log) and 'skipped=' not in log
    messages=[]
    for path in (out/'native').rglob('*.log'):
        for line in path.read_text().splitlines():
            if 'stage=revocations cause=Issuer client deadline exceeded' in line:
                assert 'phase=reply_header wait=read progress=0/4 ' in line,line
                messages.append(line)
    assert messages,'Missing native active-generation timeout diagnostic'
    loop_delays=[]
    for path in (out/'native').rglob('*.log'):
        for line in path.read_text().splitlines():
            if line.startswith('issuer TLS loop delayed '):loop_delays.append(dict(file=path.name,message=line))
    assert any(row['file'].startswith('test_server_records_long_loop_delay_without_changing_protocol-issuer-') for row in loop_delays)
    profiles[profile]=dict(loop_delays=loop_delays,cases=record['cases'],suites=record['suites'],binaries=record['binaries'],
        active_generation_diagnostics=messages)
baseline=base/'m02-issuer-timeout-baseline-01'
prior=json.loads((baseline/'review.json').read_bytes())
assert prior['status']=='missing_diagnostic_reproduced' and prior['expected_failures']==3
assert sha(baseline/'baseline.log')==prior['log_sha256'] and sha(baseline/'test_issuer_client.py')==prior['test_sha256']
assert (baseline/'test_issuer_client.py').read_bytes().replace(b'\r\n',b'\n')==(root/'source/tests/core/test_issuer_client.py').read_bytes()
for path in baseline.iterdir():add(path)
loop_baseline=base/'m02-issuer-loop-baseline-01'
loop_prior=json.loads((loop_baseline/'review.json').read_bytes())
assert loop_prior['status']=='missing_loop_diagnostic_reproduced' and loop_prior['expected_failures']==1
assert sha(loop_baseline/'baseline.log')==loop_prior['log_sha256']
assert (loop_baseline/'test_identity_tls.py').read_bytes().replace(b'\r\n',b'\n')==(root/'source/tests/core/test_identity_tls.py').read_bytes()
for path in loop_baseline.iterdir():add(path)
for path in (base/'m02-candidate02-kernel-timing-01').iterdir():add(path)
add(root/'manifest.json')
for name in manifest['changed']:add(root/'source'/name)
for path in (root/'checks').rglob('*'):
    if path.is_file():add(path)
for name in ('check-issuer-timeout-baseline-01.py','check-issuer-loop-baseline-01.py','check-issuer-timeout-02.py','collect-issuer-timeout-02.py'):add(base/name)
review=dict(status='focused_diagnostic_passed_phase_gate_pending',host='sb16',source_files=351,
    locked_files_unchanged=41,profiles=profiles,attachments=attachments,
    changed=manifest['changed'],deadlines_changed=False,existing_assertions_removed=False,
    historical_failures_resolved=False,
    limitations=['Both historical intermittent failures remain unresolved.',
        'Injected stalls verify the new diagnostic and fail-closed behavior, not the historical cause.',
        'Full M02 qualification remains pending on both hosts.'])
review_path=root/'primary-review.json'
with review_path.open('xb') as stream:stream.write((json.dumps(review,indent=2)+'\n').encode())
archive=base/'issuer-timeout-evidence-02.tar'
with tarfile.open(archive,'x') as tar:
    for name in sorted(attachments):tar.add(base/name,arcname='sb16/'+name,recursive=False)
    tar.add(review_path,arcname='primary-review.json',recursive=False)
print(json.dumps(dict(archive=str(archive),sha256=sha(archive),attachments=len(attachments),profiles={name:dict(cases=value['cases'],suites=value['suites'],loop_delays=value['loop_delays']) for name,value in profiles.items()})))
