"""Qualify supervisor descriptor handoff only after the preceding owner candidate finishes."""
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import threading
import time

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
root = r / 'local/m02-supervisor-handoff-01'
source = root / 'source'
out = root / 'qualification'
out.mkdir(exist_ok=False)
previous = r / 'local/m02-control-reply-01'
manifest = json.loads((root / 'manifest.json').read_text())
baseline = r / 'local/test_supervisor_handoff_lifetime_01.py'
world_test = r / 'local/test_control_owner_world_01.py'
after_pid = {'sb16': 1872025, 'sb16-2': 1616004}[host]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

record = dict(host=host, status='waiting', preceding_pid=after_pid, source_files=304,
    locked_files=41, source_manifest=manifest['files'], started=time.time(), profiles={},
    baseline={}, compiler_overlap=[], baseline_test_sha256=sha(baseline), world_test_sha256=sha(world_test))
stop = threading.Event()
monitor = None

def save():
    (out / 'summary.json').write_text(json.dumps(record, indent=2) + '\n')

def audit():
    for group in ('files', 'locked_files'):
        assert all(sha(source / n) == h for n, h in manifest[group].items())
    assert sha(baseline) == record['baseline_test_sha256']
    assert sha(world_test) == record['world_test_sha256']

def compilers():
    found = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            name = (p / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if name in ('cc1', 'cc1plus', 'clang', 'clang++', 'lto1'):
            found.append(dict(pid=int(p.name), name=name))
    return found

def watch():
    with (out / 'compiler-monitor.jsonl').open('x') as log:
        while not stop.is_set():
            found = compilers()
            entry = dict(time=time.time(), compilers=found)
            log.write(json.dumps(entry) + '\n')
            log.flush()
            if found:
                record['compiler_overlap'].append(entry)
            stop.wait(1)

def environment(build, src, target, server):
    env = dict(os.environ, PYTHONPATH=str(build / 'python') + ':' + str(src / 'tests/core') + ':' + str(src),
        SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build / 'spatial-xprobe'),
        SPATIAL_SECURITY_EVIDENCE=str(target / 'native'), SPATIAL_CONTROL_OWNER_WORLD_EVIDENCE=str(target / 'witness'),
        SPATIAL_EVIDENCE_DIR=str(target / 'cases'), SPATIAL_SUT=json.dumps([str(build / 'spatial-test-host')]),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    env.pop('LSAN_OPTIONS', None)
    return env

def run(name, command, target, env, results, expected=0):
    audit()
    started = time.time()
    with (target / (name + '.log')).open('xb') as log:
        result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
    results.append(dict(name=name, command=command, started=started, ended=time.time(), exit_code=result.returncode))
    save()
    print(host, target.name, name, result.returncode, flush=True)
    if result.returncode != expected:
        print((target / (name + '.log')).read_text()[-9000:], flush=True)
        raise RuntimeError(name + ' returned an unexpected result')

save()
try:
    marker = Path('/proc') / str(after_pid) / 'cmdline'
    if marker.exists() and b'qualify-control-reply-01.py' in marker.read_bytes():
        descriptor = os.pidfd_open(after_pid)
        try:
            poller = select.poll()
            poller.register(descriptor, select.POLLIN)
            while not poller.poll(2000):
                pass
        finally:
            os.close(descriptor)
    prior = json.loads((previous / 'qualification/summary.json').read_text())
    if prior['status'] != 'passed' or prior['compiler_overlap']:
        record.update(status='not_run', reason='Preceding reply qualification did not pass')
        save()
        raise SystemExit(0)
    assert not compilers()
    old = json.loads((previous / 'manifest.json').read_text())
    assert all(sha(previous / 'source' / n) == h for n, h in old['files'].items())
    server = prior['server']
    assert sha(Path(server['path'])) == server['sha256']
    record.update(server=server, preceding_summary_sha256=sha(previous / 'qualification/summary.json'),
        status='reproducing-retained-descriptors')
    save()
    for profile in ('debug', 'runtime-sanitize'):
        build = Path(prior['profiles'][profile]['build'])
        assert all(sha(build / n) == h for n, h in prior['profiles'][profile]['binaries'].items())
        target = out / ('baseline-' + profile)
        target.mkdir()
        results = []
        record['baseline'][profile] = dict(results=results)
        env = environment(build, previous / 'source', target, server)
        command = ['systemd-run', '--user', '--wait', '--pipe', '--collect',
            '--unit=spatial-handoff-baseline-' + profile + '-' + str(os.getpid()), '--property=Delegate=yes']
        command += ['--setenv=' + name + '=' + env[name] for name in
            ('PYTHONPATH', 'SPATIAL_TEST_XSERVER', 'SPATIAL_SECURITY_EVIDENCE', 'ASAN_OPTIONS', 'UBSAN_OPTIONS')]
        command += ['python3', str(baseline), str(previous / 'source'), str(build / 'spatial-authority-launch-probe'),
            str(build / 'spatial-security-auth'), str(build / 'identityd'), str(build / 'spatial-issuer-client')]
        assert not compilers()
        run('handoff', command, target, env, results, expected=1)
        text = (target / 'handoff.log').read_text()
        assert 'Ran 2 tests' in text and 'FAILED (failures=2)' in text and 'ERROR:' not in text
        assert len(re.findall(r'^FAIL: test_', text, re.M)) == 2
        assert 'Supervisor retained an endpoint after its only child exited' in text
        record['baseline'][profile]['retained_copies_reproduced'] = True
        save()
    for profile in ('debug', 'runtime-sanitize'):
        build = root / profile / 'build'
        target = out / profile
        target.mkdir()
        results = []
        record['profiles'][profile] = dict(build=str(build), results=results)
        env = environment(build, source, target, server)
        record['status'] = 'building-' + profile
        save()
        run('configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Debug',
            '-DSPATIAL_INPUT_TESTS=ON', '-DSPATIAL_SANITIZERS=' + ('ON' if profile == 'runtime-sanitize' else 'OFF'),
            '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache'], target, env, results)
        run('build', ['cmake', '--build', str(build), '--parallel', '2'], target, env, results)
        record['profiles'][profile]['binaries'] = {n: sha(build / n) for n in
            ('workspaced', 'worldd', 'identityd', 'spatialwm', 'spatial-test-host', 'spatial-issuer-client',
             'spatial-security-auth', 'spatial-authority-supervisor', 'spatial-authority-launch-probe', 'spatial-control-reply-probe')}
        save()
    assert not compilers()
    monitor = threading.Thread(target=watch)
    monitor.start()
    record['status'] = 'qualifying'
    save()
    for profile in ('debug', 'runtime-sanitize'):
        build = root / profile / 'build'
        target = out / profile
        env = environment(build, source, target, server)
        results = record['profiles'][profile]['results']
        commands = [
            ('harness', ['python3', '-m', 'unittest', 'discover', '-s', 'tests/harness', '-t', '.', '-q']),
            ('handoff', ['ctest', '--test-dir', str(build), '-V', '-R', '^supervisor-handoff-lifetime$']),
            ('world', ['python3', str(world_test), str(build / 'identityd'), str(build / 'spatial-issuer-client'),
                str(build / 'worldd'), str(build / 'spatial-security-auth')]),
            ('core', ['ctest', '--test-dir', str(build), '-V', '-E', '^supervisor-handoff-lifetime$']),
            ('P10', ['python3', 'tools/with_test_display.py', '--', 'python3', 'tools/gate.py', '--through', 'P10',
                '--report', str(target / 'P10.json')])]
        for name, command in commands:
            assert not compilers()
            run(name, command, target, env, results)
        assert all(sha(build / n) == h for n, h in record['profiles'][profile]['binaries'].items())
    audit()
    assert not record['compiler_overlap']
    record['status'] = 'passed'
except SystemExit:
    raise
except BaseException:
    record['status'] = 'failed'
    raise
finally:
    stop.set()
    if monitor:
        monitor.join()
    record['ended'] = time.time()
    save()
