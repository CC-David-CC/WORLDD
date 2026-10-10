"""Run the real recovery-gap witness only after this host's full qualification passes."""
import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import tarfile
import time

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
out = r / 'local/control-recovery-baseline-02'
out.mkdir(exist_ok=False)
qualifier = r / 'local/crash-fencing-serial-02'
launch = json.loads((r / 'local/crash-fencing-serial-driver-02/launch.json').read_text())
expected_pid = {'sb16': 1544923, 'sb16-2': 1334189}[host]
assert launch['pid'] == expected_pid
record = dict(host=host, state='waiting', qualifier_pid=expected_pid,
              production_recovery_qualified=False, started=time.time())

def save():
    (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

save()
try:
    while True:
        try:
            command = (Path('/proc') / str(expected_pid) / 'cmdline').read_bytes()
        except FileNotFoundError:
            break
        if b'qualify-crash-fencing-serial-02.py' not in command:
            break
        time.sleep(2)
    qualification = json.loads((qualifier / 'summary.json').read_text())
    assert qualification['host'] == host
    if qualification['qualification'] != 'passed' or qualification['compiler_overlap']:
        record.update(state='not_run', reason='Preceding qualification did not pass',
                      preceding_qualification=qualification['qualification'])
        save()
        raise SystemExit(0)
    record['preceding_qualification_sha256'] = sha(qualifier / 'summary.json')
    source = r / 'local/m02-supervisor-crash-fencing-01/source'
    build = source.parent / 'debug/build'
    manifest = qualification['source_manifest']
    assert len(manifest) == 294
    assert all(sha(source / name) == digest for name, digest in manifest.items())
    binaries = dict(qualification['profiles']['debug']['binaries'])
    binaries['spatial-security-auth'] = sha(build / 'spatial-security-auth')
    assert all(sha(build / name) == digest for name, digest in binaries.items())
    server = qualification['profiles']['debug']['server']
    assert sha(Path(server['path'])) == server['sha256']
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            name = (proc / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        assert name not in ('cc1', 'cc1plus', 'clang', 'clang++', 'lto1'), 'Compiler workload is active'
    script = r / 'local/test_control_recovery_baseline_01.py'
    command = ['python3', str(script), str(build / 'identityd'),
               str(build / 'spatial-issuer-client'), str(build / 'worldd'), str(build / 'spatial-security-auth')]
    environment = dict(os.environ,
        PYTHONPATH=os.pathsep.join(map(str, [build / 'python', source / 'tests/core', source])),
        SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build / 'spatial-xprobe'),
        SPATIAL_CRASH_EVIDENCE=str(out / 'witness'), SPATIAL_SECURITY_EVIDENCE=str(out / 'logs'),
        LIBGL_ALWAYS_SOFTWARE='1', ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
        UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    record.update(state='running', command=command, source_manifest=manifest, server=server,
                  binaries=binaries, script_sha256=sha(script), profile='Debug')
    save()
    with (out / 'run.log').open('xb') as log:
        child = subprocess.Popen(command, cwd=source, env=environment,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        record['diagnostic_pid'] = child.pid
        save()
        try:
            exit_code = child.wait(timeout=180)
        except subprocess.TimeoutExpired:
            record['timed_out'] = True
            # This group was created above for this diagnostic. Never target another session.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=10)
            raise
    assert all(sha(source / name) == digest for name, digest in manifest.items())
    assert all(sha(build / name) == digest for name, digest in binaries.items())
    record.update(state='diagnostic_passed' if exit_code == 0 else 'diagnostic_failed',
                  exit_code=exit_code)
except BaseException:
    if record['state'] in ('waiting', 'running'):
        record['state'] = 'driver_error'
    raise
finally:
    record['ended'] = time.time()
    save()
    with tarfile.open(r / ('local/control-recovery-baseline-02-' + host + '.tar.gz'), 'w:gz') as archive:
        archive.add(out, arcname='baseline-02/' + host)
    print(json.dumps({k: record[k] for k in ('host', 'state', 'production_recovery_qualified')}), flush=True)
