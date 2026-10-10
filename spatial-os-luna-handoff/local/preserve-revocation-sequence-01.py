"""Preserve the original-order diagnostic without changing its raw evidence."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
remote = '/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local'
out = root / 'evidence/control-recovery-2026-10-09/revocation-sequence-01'
out.mkdir(parents=True, exist_ok=False)
sha = lambda data: hashlib.sha256(data).hexdigest()
subprocess.run(['scp', '-q', '-r', 'sb16:' + remote + '/m02-revocation-sequence-01', str(out / 'sb16')], check=True)
subprocess.run(['scp', '-q', 'sb16:' + remote + '/m02-approval-interruption-02/checks/review.json',
    str(out / 'original-review.json')], check=True)
review = json.loads((out / 'sb16/review.json').read_bytes())
assert review['status'] == 'not_reproduced_cause_unconfirmed'
assert review['prefix_exit_code'] == review['test_exit_code'] == review['trace_exit_code'] == 0
assert sha((out / 'original-review.json').read_bytes()) == review['original_review_sha256']
for name, digest in review['attachments'].items():
    path = out / 'sb16' / name
    assert path.resolve().is_relative_to((out / 'sb16').resolve())
    assert sha(path.read_bytes()) == digest, name
assert review['results'][0]['tests'] == 14 and review['results'][0]['passed']
assert not review['source_changed'] and not review['deadlines_changed']
assert not review['original_failure_resolved']
timing = (out / 'sb16/timing.log').read_text()
events = json.loads((out / 'sb16/case-timing.json').read_bytes())
target = 'test_revocation_requires_child_acknowledgement_with_automatic_delivery'
interval = [event['monotonic_ns'] for event in events if event['test'].endswith('.' + target)]
assert len(interval) == 2 and interval[0] < interval[1]
assert 'ROOT_EXIT' in timing and 'TRACE_DEADLINE' not in timing
target_lines = [line for line in timing.splitlines() if (match := re.search(r'\bns=(\d+)', line))
    and interval[0] <= int(match[1]) <= interval[1]]
issuer_waits = [line for line in timing.splitlines() if line.startswith(('WAIT ', 'SLOW ')) and 'comm=identityd ' in line]
maximum = int(re.search(r'@maximum_us\[identityd, tracepoint:syscalls:sys_exit_fdatasync\]: (\d+)', timing)[1])
prefix_log = (out / 'sb16/prefix.log').read_text()
assert '\nOK\n' in prefix_log
prefix_cases = int(re.search(r'Ran (\d+) tests', prefix_log)[1])
attachments = {}
for path in out.rglob('*'):
    if not path.is_file(): continue
    data = path.read_bytes()
    assert b'-----BEGIN ' + b'PRIVATE KEY-----' not in data
    assert not re.search(rb'Bearer ' + rb'fleet-[A-Za-z0-9_-]{12,}', data)
    attachments[path.relative_to(out).as_posix()] = sha(data)
primary = dict(status='not_reproduced_cause_unconfirmed', host='sb16', profile='runtime-sanitize',
    original_failure_resolved=False, production_changed=False, deadlines_changed=False,
    preceding_suite_cases=prefix_cases, supervisor_cases=14, failed=0, skipped=0,
    issuer_max_fdatasync_us=maximum, issuer_waits_at_least_100ms=issuer_waits,
    target_case_interval_ns=interval, target_case_wait_lines=target_lines,
    interpretation='The original preceding suite and case order did not reproduce the timeout. Successful timing does not explain the historical failure.',
    next_action='Add bounded transport phase and elapsed-time diagnostics, then exercise deterministic stalled replies and affected integration paths.',
    attachments=attachments)
(out / 'primary-review.json').write_bytes((json.dumps(primary, indent=2) + '\n').encode())
print(json.dumps(dict(attachments=len(attachments), preceding_suite_cases=prefix_cases,
    supervisor_cases=14, issuer_max_fdatasync_us=maximum, issuer_slow_events=len(issuer_waits))))
