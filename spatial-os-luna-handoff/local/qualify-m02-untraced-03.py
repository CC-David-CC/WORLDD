"""Qualify unchanged candidate02 binaries after preserving the prior terminal run."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
gate = base/'m02-phase-gate-02'
root = base/'m02-untraced-03'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
lock = (base/'m02-phase-qualification.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
prior = json.loads((gate/'summary.json').read_bytes())
assert 'ended' in prior and prior['status'] == 'failed', 'Only retry a terminal failed run'
assert not prior['compiler_overlap'] and not prior['monitor_errors']
archive = base/('m02-phase-gate-evidence-02-'+host+'.tar.gz')
assert len(sys.argv) == 2 and sha(archive) == sys.argv[1], 'Prior evidence archive is not verified'
prior_review = json.loads((gate/'primary-review.json').read_bytes())
assert prior_review['host'] == host and prior_review['run_status'] == prior['status']
manifest = json.loads((gate/'manifest.json').read_bytes())
assert manifest['candidate'] == prior['candidate'] == 'm02-phase-gate-02'
assert manifest['commit'] == prior['commit'] == prior_review['commit']
source = gate/'source'
server = prior['server']
for profile in ('debug', 'runtime-sanitize'):
    commands = {entry['label']:entry for entry in prior['profiles'][profile]['commands']}
    assert commands['configure']['exit_code'] == commands['build']['exit_code'] == 0
    assert prior['profiles'][profile]['binaries']
    cache = (gate/profile/'build/CMakeCache.txt').read_text()
    assert 'CMAKE_BUILD_TYPE:STRING=Debug' in cache
    assert 'SPATIAL_SANITIZERS:BOOL='+('ON' if profile=='runtime-sanitize' else 'OFF') in cache

def workloads():
    found = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            name = (process/'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if name in ('cc1', 'cc1plus', 'clang', 'clang++', 'lto1', 'ninja', 'ctest', 'bpftrace'):
            found.append(dict(pid=int(process.name), name=name))
    return found

def audit(binaries=False):
    assert all(sha(source/name) == digest for name,digest in manifest['files'].items())
    assert all(manifest['files'][name] == digest for name,digest in manifest['locked_files'].items())
    assert sha(Path(server['path'])) == server['sha256']
    assert sha(gate/'summary.json') == record['prior_summary_sha256']
    if binaries:
        for profile in ('debug', 'runtime-sanitize'):
            assert all(sha(gate/profile/'build'/name) == digest
                for name,digest in prior['profiles'][profile]['binaries'].items())

assert not workloads(), 'Another build, qualification, or tracer is active'
root.mkdir(exist_ok=False)
(root/'manifest.json').write_bytes((gate/'manifest.json').read_bytes())
record = dict(host=host, candidate=manifest['candidate'], attempt='m02-untraced-03',
    commit=manifest['commit'], source_root=str(source), started=time.time(), runner_pid=os.getpid(),
    runner_sha256=sha(Path(__file__)), status='preparing_writeback', server=server,
    source_files=len(manifest['files']), locked_files=len(manifest['locked_files']),
    archive_sha256=prior['archive_sha256'], prior_summary_sha256=sha(gate/'summary.json'),
    prior_review_sha256=sha(gate/'primary-review.json'), prior_evidence_archive_sha256=sys.argv[1],
    build_roots={profile:str(gate/profile/'build') for profile in ('debug', 'runtime-sanitize')},
    profiles={profile:dict(status='tests_unrun', commands=[], binaries=prior['profiles'][profile]['binaries'])
        for profile in ('debug', 'runtime-sanitize')},
    compiler_overlap=[], historical_failures_resolved=False,
    remaining=['Review full local results', 'Cross-host qualification', 'Historical failure disposition'],
    reuse='Source and executable bytes are unchanged. Prior terminal results are archived before CTest reuses its Testing directory.')

def save():
    temp = root/'summary.json.tmp'
    temp.write_bytes((json.dumps(record, indent=2)+'\n').encode())
    temp.replace(root/'summary.json')

def sample():
    found = workloads()
    item = dict(wall_time=time.time(), monotonic_ns=time.monotonic_ns(), phase=record['status'],
        compilers=[entry for entry in found if entry['name'] not in ('ctest', 'bpftrace')],
        disk_free_bytes=os.statvfs(root).f_bavail*os.statvfs(root).f_frsize)
    for name in ('pressure/cpu', 'pressure/io', 'pressure/memory', 'meminfo', 'vmstat', 'diskstats'):
        item[name] = (Path('/proc')/name).read_text()
    return item

stop = threading.Event()
monitor_errors = []

def monitor():
    try:
        with (root/'resource-monitor.jsonl').open('x') as log:
            while not stop.is_set():
                item = sample()
                log.write(json.dumps(item)+'\n')
                log.flush()
                if record['status'] == 'qualifying' and item['compilers']:
                    record['compiler_overlap'].append(dict(time=item['wall_time'], compilers=item['compilers']))
                stop.wait(1)
    except BaseException as error:
        monitor_errors.append(type(error).__name__+': '+str(error))

def environment(profile):
    build = gate/profile/'build'
    target = root/profile/'checks'
    env = {name:value for name,value in os.environ.items()
        if name not in ('LSAN_OPTIONS', 'CTEST_PARALLEL_LEVEL', 'DISPLAY', 'SPATIAL_TEST_DISPLAY')}
    env.update(SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build/'spatial-xprobe'),
        SPATIAL_SECURITY_EVIDENCE=str(target/'native'), PYTHONPATH=str(build/'python')+':'+str(source),
        SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]), SPATIAL_EVIDENCE_DIR=str(target/'cases'),
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    return env

def run(profile, label, command, timeout):
    audit()
    assert not workloads(), 'Another compiler, qualification, or tracer is running'
    assert not monitor_errors, monitor_errors
    target = root/profile/'checks'
    entry = dict(label=label, argv=command, started=time.time(), traced=False, qualification=True)
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
    audit(binaries=True)
    assert not workloads()
    sync = dict(argv=['sync', '-f', str(gate)], before=sample(), started=time.time())
    record['writeback_boundary'] = sync
    save()
    with (root/'writeback-sync.log').open('xb') as log:
        result = subprocess.run(sync['argv'], stdout=log, stderr=subprocess.STDOUT, timeout=120)
    sync.update(exit_code=result.returncode, ended=time.time(), after=sample(), log_sha256=sha(root/'writeback-sync.log'))
    save()
    assert result.returncode == 0, 'Build-filesystem synchronization failed'
    assert not workloads() and not monitor_errors
    record['status'] = 'qualifying'
    save()
    for profile in ('debug', 'runtime-sanitize'):
        target = root/profile/'checks'
        target.mkdir(parents=True, exist_ok=False)
        build = gate/profile/'build'
        run(profile, 'ctest-inventory', ['ctest', '--test-dir', str(build), '--show-only=json-v1'], 60)
        inventory = json.loads((target/'ctest-inventory.log').read_bytes())
        names = [test['name'] for test in inventory['tests']]
        assert names == prior['profiles']['debug']['selected_ctest_suites'] and len(set(names)) == 124
        record['profiles'][profile]['selected_ctest_suites'] = names
        run(profile, 'harness-inventory', ['python3', '-c',
            'import unittest,json; l=unittest.TestLoader(); s=l.discover("tests/harness",top_level_dir="."); '
            'walk=lambda s:[i for t in s for i in (walk(t) if isinstance(t,unittest.TestSuite) else [t.id()])]; '
            'assert not l.errors,l.errors; print(json.dumps(walk(s)))'], 60)
        harness_ids = json.loads((target/'harness-inventory.log').read_bytes())
        assert harness_ids == prior['profiles']['debug']['selected_harness_cases'] and len(set(harness_ids)) == 51
        record['profiles'][profile]['selected_harness_cases'] = harness_ids
        run(profile, 'acceptance-inventory', ['python3', 'tools/gate.py', '--through', 'P10', '--list'], 60)
        acceptance_ids = [line.split('\t')[-1] for line in (target/'acceptance-inventory.log').read_text().splitlines()]
        assert acceptance_ids == prior['profiles']['debug']['selected_acceptance_cases'] and len(set(acceptance_ids)) == 151
        record['profiles'][profile]['selected_acceptance_cases'] = acceptance_ids
        record['profiles'][profile]['status'] = 'qualifying'
        save()
        run(profile, 'harness', ['python3', '-m', 'unittest', 'discover', '-s', 'tests/harness', '-t', '.', '-v'], 180)
        run(profile, 'core', ['ctest', '--test-dir', str(build), '--parallel', '1', '-V',
            '--output-junit', str(target/'core.xml')], 14400)
        run(profile, 'P10', ['python3', 'tools/with_test_display.py', '--', 'python3', 'tools/gate.py',
            '--through', 'P10', '--report', str(target/'P10.json')], 1800)
        audit(binaries=True)
        record['profiles'][profile]['status'] = 'commands_passed_results_need_review'
        save()
    record['status'] = 'commands_passed_results_need_review'
except BaseException as error:
    record.update(status='failed', failure=type(error).__name__+': '+str(error))
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
