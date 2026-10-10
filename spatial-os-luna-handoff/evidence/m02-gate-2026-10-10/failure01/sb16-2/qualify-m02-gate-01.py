"""Build first, then qualify one frozen M02 candidate on an authorized host."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time

host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
archive = base / 'm02-phase-gate-source-01.tar'
assert sha(archive) == sys.argv[1]
lock = (base / 'm02-phase-qualification.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
assert shutil.disk_usage(base).free > 12 * 2**30, 'Insufficient space for isolated builds'
root = base / 'm02-phase-gate-01'
root.mkdir(exist_ok=False)
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({member.name for member in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        path = root.joinpath(*name.parts)
        assert path.resolve().is_relative_to(root.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(tar.extractfile(member).read())
manifest = json.loads((root / 'manifest.json').read_bytes())
source = root / 'source'
server = json.loads((base / 'm04-surface-identity-build-01/run.json').read_bytes())['binaries']['Xvfb']
record = dict(host=host, candidate=manifest['candidate'], commit=manifest['commit'],
    archive_sha256=sys.argv[1], status='building', started=time.time(), runner_pid=os.getpid(),
    source_files=len(manifest['files']), locked_files=len(manifest['locked_files']), server=server,
    profiles={}, compiler_overlap=[], historical_failures_resolved=False,
    remaining=['Cross-host and other M02 evidence reassessment', 'Historical intermittent failure explanation'])

def save():
    temp = root / 'summary.json.tmp'
    temp.write_bytes((json.dumps(record, indent=2)+'\n').encode())
    temp.replace(root / 'summary.json')

def workloads():
    found = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            name = (process / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if name in ('cc1', 'cc1plus', 'clang', 'clang++', 'lto1', 'ninja', 'ctest'):
            found.append(dict(pid=int(process.name), name=name))
    return found

def audit():
    assert all(sha(source/name) == digest for name, digest in manifest['files'].items())
    assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
    assert sha(Path(server['path'])) == server['sha256']

stop = threading.Event()
monitor_errors = []

def monitor():
    try:
        with (root / 'resource-monitor.jsonl').open('x') as log:
            while not stop.is_set():
                found = workloads()
                compilers = [item for item in found if item['name'] != 'ctest']
                item = dict(wall_time=time.time(), monotonic_ns=time.monotonic_ns(), phase=record['status'],
                    compilers=compilers, disk_free_bytes=shutil.disk_usage(root).free)
                for name in ('pressure/cpu', 'pressure/io', 'pressure/memory', 'meminfo', 'vmstat', 'diskstats'):
                    item[name] = (Path('/proc')/name).read_text()
                log.write(json.dumps(item)+'\n')
                log.flush()
                if record['status'] == 'qualifying' and compilers:
                    record['compiler_overlap'].append(dict(time=item['wall_time'], compilers=compilers))
                stop.wait(1)
    except BaseException as error:
        monitor_errors.append(type(error).__name__ + ': ' + str(error))

def environment(profile):
    build = root/profile/'build'
    target = root/profile/'checks'
    env = {name: value for name, value in os.environ.items()
        if name not in ('LSAN_OPTIONS', 'CTEST_PARALLEL_LEVEL', 'DISPLAY', 'SPATIAL_TEST_DISPLAY')}
    env.update(CCACHE_BASEDIR=str(root), SPATIAL_TEST_XSERVER=server['path'],
        SPATIAL_XPROBE=str(build/'spatial-xprobe'), SPATIAL_SECURITY_EVIDENCE=str(target/'native'),
        PYTHONPATH=str(build/'python')+':'+str(source), SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),
        SPATIAL_EVIDENCE_DIR=str(target/'cases'), ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
        UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    return env

def run(profile, label, command, timeout):
    audit()
    assert not workloads(), 'Another compiler or qualification job is running'
    assert not monitor_errors, monitor_errors
    target = root/profile/'checks'
    entry = dict(label=label, argv=command, started=time.time())
    record['profiles'][profile]['commands'].append(entry)
    save()
    with (target/(label+'.log')).open('xb') as log:
        result = subprocess.run(command, cwd=source, env=environment(profile),
            stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    entry.update(exit_code=result.returncode, ended=time.time(), sha256=sha(target/(label+'.log')))
    save()
    print(host, profile, label, result.returncode, flush=True)
    if result.returncode:
        print((target/(label+'.log')).read_text()[-10000:], flush=True)
        raise RuntimeError(profile+' '+label+' failed')

save()
thread = threading.Thread(target=monitor)
thread.start()
try:
    audit()
    assert not workloads(), 'Existing workload prevents candidate preparation'
    for profile in ('debug', 'runtime-sanitize'):
        target = root/profile/'checks'
        target.mkdir(parents=True, exist_ok=False)
        build = root/profile/'build'
        record['profiles'][profile] = dict(status='building', commands=[])
        run(profile, 'configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
            '-DCMAKE_BUILD_TYPE=Debug', '-DBUILD_TESTING=ON', '-DSPATIAL_INPUT_TESTS=ON',
            '-DSPATIAL_SANITIZERS='+('ON' if profile=='runtime-sanitize' else 'OFF'),
            '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache'], 180)
        run(profile, 'build', ['cmake', '--build', str(build), '--parallel', '2'], 7200)
        record['profiles'][profile]['binaries'] = {path.name: sha(path) for path in build.iterdir()
            if path.is_file() and os.access(path, os.X_OK)}
        record['profiles'][profile]['status'] = 'built_tests_unrun'
        save()
    record['status'] = 'qualifying'
    save()
    for profile in ('debug', 'runtime-sanitize'):
        target = root/profile/'checks'
        build = root/profile/'build'
        run(profile, 'ctest-inventory', ['ctest', '--test-dir', str(build), '--show-only=json-v1'], 60)
        inventory = json.loads((target/'ctest-inventory.log').read_bytes())
        names = [test['name'] for test in inventory['tests']]
        assert len(names) == len(set(names)) and len(names) >= 101
        record['profiles'][profile]['selected_ctest_suites'] = names
        run(profile, 'harness-inventory', ['python3', '-c',
            'import unittest,json; l=unittest.TestLoader(); s=l.discover("tests/harness",top_level_dir="."); '
            'walk=lambda s:[i for t in s for i in (walk(t) if isinstance(t,unittest.TestSuite) else [t.id()])]; '
            'assert not l.errors,l.errors; print(json.dumps(walk(s)))'], 60)
        harness_ids = json.loads((target/'harness-inventory.log').read_bytes())
        assert len(harness_ids) == 51 and len(set(harness_ids)) == 51
        record['profiles'][profile]['selected_harness_cases'] = harness_ids
        run(profile, 'acceptance-inventory', ['python3', 'tools/gate.py', '--through', 'P10', '--list'], 60)
        acceptance_ids = [line.split('\t')[-1] for line in (target/'acceptance-inventory.log').read_text().splitlines()]
        assert len(acceptance_ids) == 151 and len(set(acceptance_ids)) == 151
        record['profiles'][profile]['selected_acceptance_cases'] = acceptance_ids
        record['profiles'][profile]['status'] = 'qualifying'
        save()
        run(profile, 'harness', ['python3', '-m', 'unittest', 'discover', '-s', 'tests/harness', '-t', '.', '-v'], 180)
        run(profile, 'core', ['ctest', '--test-dir', str(build), '--parallel', '1', '-V',
            '--output-junit', str(target/'core.xml')], 14400)
        run(profile, 'P10', ['python3', 'tools/with_test_display.py', '--', 'python3', 'tools/gate.py',
            '--through', 'P10', '--report', str(target/'P10.json')], 1800)
        assert all(sha(build/name) == digest for name, digest in record['profiles'][profile]['binaries'].items())
        audit()
        record['profiles'][profile]['status'] = 'commands_passed_results_need_review'
        save()
    record['status'] = 'commands_passed_results_need_review'
except BaseException as error:
    record['status'] = 'failed'
    record['failure'] = type(error).__name__ + ': ' + str(error)
    raise
finally:
    stop.set()
    thread.join()
    record['monitor_errors'] = monitor_errors
    if monitor_errors or record['compiler_overlap']:
        record['status'] = 'invalid_environment_or_monitor'
    record['ended'] = time.time()
    save()
    print(host, record['status'], flush=True)
