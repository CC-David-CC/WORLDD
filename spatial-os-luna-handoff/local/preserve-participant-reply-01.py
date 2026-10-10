"""Retain the bounded issuer timing investigation, including its inconclusive result."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

root=Path(__file__).resolve().parents[1]
out=root/'evidence/control-recovery-2026-10-09/participant-reply-timing-01'
out.mkdir(parents=True,exist_ok=False)
subprocess.run(['scp','-q','-r',
    'sb16:/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m02-participant-reply-timing-01',
    str(out/'sb16')],check=True)
sha=lambda data:hashlib.sha256(data).hexdigest()
review=json.loads((out/'sb16/review.json').read_bytes())
assert review['status']=='not_reproduced_cause_unconfirmed'
assert review['prefix_exit_code']==review['exit_code']==review['trace_exit_code']==0
assert len(review['results'])==3 and all(row['passed'] and row['tests']==16 for row in review['results'])
for name,digest in review['attachments'].items():
    path=out/'sb16'/name
    assert path.resolve().is_relative_to((out/'sb16').resolve())
    assert sha(path.read_bytes())==digest,name
timing=(out/'sb16/timing.log').read_text()
assert 'ROOT_EXIT' in timing and 'TRACE_DEADLINE' not in timing
slow=[line for line in timing.splitlines() if line.startswith(('SLOW ','WAIT ')) and 'comm=identityd ' in line]
maximum={name:int(re.search(r'@maximum_us\[identityd, tracepoint:syscalls:sys_exit_'+name+r'\]: (\d+)',timing)[1])
    for name in ('fsync','fdatasync')}
attachments={}
for path in out.rglob('*'):
    if not path.is_file():continue
    data=path.read_bytes()
    assert b'-----BEGIN '+b'PRIVATE KEY-----' not in data
    assert not re.search(rb'Bearer '+rb'fleet-[A-Za-z0-9_-]{12,}',data)
    attachments[path.relative_to(out).as_posix()]=sha(data)
primary=dict(status=review['status'],historical_failure_resolved=False,source_changed=False,deadlines_changed=False,
    suite_attempts=3,passed_cases=48,failed_cases=0,issuer_syscall_maximum_us=maximum,
    issuer_waits_at_least_100ms=slow,
    interpretation='No issuer stall of at least 100 ms occurred in the successful traced attempts. The untraced 705 ms reply-header timeout remains unexplained.',
    next_action='Capture issuer TLS read/write progress and readiness alongside kernel timing if the current failed suite recurs. Do not increase deadlines or treat repetitions as a fix.',
    attachments=attachments)
(out/'primary-review.json').write_bytes((json.dumps(primary,indent=2)+'\n').encode())
print(json.dumps(dict(attachments=len(attachments),passed_cases=48,issuer_syscall_maximum_us=maximum,
    issuer_slow_events=len(slow))))
