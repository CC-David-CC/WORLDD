"""Run only the corrected handle checks against unchanged candidate01 binaries."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

root = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] == 'sb16'
candidate = root / 'local/m02-retained-child-handle-01'
helper = root / 'local/test_retained_child_handle_02.py'
out = root / 'local/m02-retained-child-handle-focused-02'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_idle():
    for process in Path('/proc').iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            name = (process / 'comm').read_text().strip()
            args = (process / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        assert name not in ('cc1', 'cc1plus', 'clang', 'clang++', 'lto1', 'ctest'), (process.name, name)
        assert not any(Path(a.decode(errors='replace')).name in (
            'qualify-control-admin-01.py', 'qualify-retained-child-handle-01.py') for a in args), process.name


manifest = json.loads((candidate / 'manifest.json').read_text())
previous = json.loads((candidate / 'qualification/summary.json').read_text())
assert previous['status'] == 'failed'
assert_idle()
for group in ('files', 'locked_files'):
    assert all(sha(candidate / 'source' / name) == digest for name, digest in manifest[group].items())
out.mkdir(exist_ok=False)
record = dict(host='sb16', status='running', started=time.time(),
    selection_reason='Only the cleanup oracle changed. Exercise all five handle scenarios in both existing build profiles.',
    original_failure='m02-retained-child-handle-01/qualification/debug/handle.log',
    source_manifest_sha256=sha(candidate / 'manifest.json'),
    original_summary_sha256=sha(candidate / 'qualification/summary.json'),
    helper_sha256=sha(helper), runner_sha256=sha(Path(__file__)), profiles={},
    full_qualification='Unrun after this correction; required at the coherent phase gate.')


def save():
    (out / 'review.json').write_text(json.dumps(record, indent=2) + '\n')


save()
try:
    for profile in ('debug', 'runtime-sanitize'):
        assert_idle()
        probe = candidate / profile / 'build/spatial-retained-child-handle-probe'
        digest = sha(probe)
        assert digest == previous['profiles'][profile]['binaries'][probe.name]
        command = ['systemd-run', '--user', '--wait', '--pipe', '--collect',
            '--unit=spatial-retained-handle-focused-02-' + profile, '--property=Delegate=yes',
            '--setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
            '--setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
            '--setenv=LSAN_OPTIONS=', '/usr/bin/python3', str(helper), str(probe)]
        log = out / (profile + '.log')
        with log.open('xb') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=120)
        content = log.read_text()
        record['profiles'][profile] = dict(command=command, exit_code=result.returncode,
            probe_sha256=digest, log_sha256=sha(log), checks=5)
        save()
        print(profile, result.returncode, content, flush=True)
        assert result.returncode == 0 and 'Ran 5 tests in ' in content and '\nOK\n' in content
        assert not any(marker in content for marker in ('ERROR:', 'FAIL:', 'AddressSanitizer:', 'LeakSanitizer:', 'runtime error:'))
        assert sha(probe) == digest and sha(helper) == record['helper_sha256']
        assert_idle()
    record['status'] = 'passed'
except BaseException:
    record['status'] = 'failed'
    raise
finally:
    record['ended'] = time.time()
    save()
