"""Preserve the new real revocation timeout before any further diagnosis."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0]=='sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m02-issuer-timeout-01'
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
manifest=json.loads((root/'manifest.json').read_bytes())
assert all(sha(root/'source'/name)==digest for name,digest in manifest['files'].items())
records={}
for profile in ('debug','runtime-sanitize'):
    where=root/'checks'/profile
    record=json.loads((where/'review.json').read_bytes())
    assert all(sha(root/profile/'build'/name)==digest for name,digest in record['binaries'].items())
    assert record['source']==manifest['files']
    for item in record['commands']:
        assert sha(where/(item['label']+'.log'))==item['sha256']
    records[profile]=record
assert records['debug']['status']=='passed'
assert records['runtime-sanitize']['status']=='failed'
failed=root/'checks/runtime-sanitize/identity-participant-runtime.log'
assert 'FAILED (failures=1)' in failed.read_text() and 'Ran 16 tests' in failed.read_text()
cause=root/'checks/runtime-sanitize/native/test_real_barrier_waits_for_explicit_application_acknowledgement-participant-0.log'
assert cause.read_text().strip()=='authority participant failed: code=26 request=0 stage=revocations cause=Issuer client deadline exceeded phase=reply_header wait=read progress=0/4 elapsed_ms=705 gap_ms=10 tick_ms=0'
paths=[root/'manifest.json']+[root/'source'/name for name in manifest['changed']]
paths += [path for path in (root/'checks').rglob('*') if path.is_file()]
paths += list((base/'m02-issuer-timeout-baseline-01').iterdir())
paths += [base/name for name in ('check-issuer-timeout-baseline-01.py','check-issuer-timeout-01.py','collect-issuer-timeout-failure01.py')]
attachments={}
for path in paths:
    assert path.is_file() and not path.is_symlink() and path.suffix in ('.json','.log','.pb','.frame','.txt','.py','.cpp')
    data=path.read_bytes()
    assert b'-----BEGIN '+b'PRIVATE KEY-----' not in data
    assert not re.search(rb'Bearer '+rb'fleet-[A-Za-z0-9_-]{12,}',data)
    attachments[path.relative_to(base).as_posix()]=sha(path)
review=dict(status='active_revocation_timeout_reproduced',source_files=len(manifest['files']),
    locked_files_unchanged=len(manifest['locked_files']),debug_suites=records['debug']['suites'],
    sanitizer_passed_suites=records['runtime-sanitize']['suites'],
    sanitizer_failed_suite=dict(name='identity-participant-runtime',passed=15,failed=1),
    sanitizer_unrun_suites=['authority-approval-interruption','authority-supervisor'],
    cause=cause.read_text().strip(),deadlines_changed=False,root_cause_confirmed=False,
    next_action='Trace the current participant suite with kernel syscall and scheduling timing. Correlate the actual failed case if it recurs.',
    attachments=attachments)
review_path=root/'failure-review.json'
with review_path.open('xb') as stream:stream.write((json.dumps(review,indent=2)+'\n').encode())
archive=base/'issuer-timeout-failure01.tar'
with tarfile.open(archive,'x') as tar:
    for name in sorted(attachments):tar.add(base/name,arcname='sb16/'+name,recursive=False)
    tar.add(review_path,arcname='primary-review.json',recursive=False)
print(json.dumps(dict(sha256=sha(archive),attachments=len(attachments),
    debug_suites=review['debug_suites'],sanitizer_passed_suites=review['sanitizer_passed_suites'])))
