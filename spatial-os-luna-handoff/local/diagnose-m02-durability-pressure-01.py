"""Compare one unchanged native revocation case before, during, and after bounded I/O."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest

BASE = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
GATE = BASE / 'm02-phase-gate-02'
SOURCE = GATE / 'source'
BUILD = GATE / 'runtime-sanitize/build'
ROOT = BASE / 'm02-durability-pressure-01'
METHOD = 'test_revocation_requires_child_acknowledgement_with_automatic_delivery'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def writer(out):
    """Use at most 512 MiB of storage and 2 GiB of buffered writes."""
    assert out.parent == ROOT and out.name == 'pressure'
    started = time.monotonic()
    block = os.urandom(4 * 1024 * 1024)
    total = 0
    with (out / 'writer.jsonl').open('x') as log:
        def event(**fields):
            log.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(), **fields)) + '\n')
            log.flush()
        event(event='start', pid=os.getpid())
        fd = os.open(out / 'scratch.bin', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            for index in range(4):
                os.lseek(fd, 0, os.SEEK_SET)
                for chunk in range(128):
                    if (out / 'writer-stop').exists() or time.monotonic() - started >= 20:
                        event(event='stopped', bytes_written=total)
                        return
                    view = memoryview(block)
                    while view:
                        written = os.write(fd, view)
                        if not written:
                            raise RuntimeError('Scratch write made no progress')
                        view = view[written:]
                        total += written
                    if total == 64 * 1024 * 1024:
                        event(event='ready', bytes_written=total)
                        (out / 'writer-ready').touch()
                before = time.monotonic_ns()
                os.fdatasync(fd)
                event(event='fdatasync', pass_index=index, duration_ns=time.monotonic_ns()-before,
                      bytes_written=total)
            event(event='limit_reached', bytes_written=total)
        finally:
            os.close(fd)


def worker(out):
    """Wrap only the pressure trigger. Execute the original test method unchanged."""
    sys.path.insert(0, str(SOURCE / 'tests/native'))
    import test_authority_supervisor as original
    case = original.AuthoritySupervisorTests(METHOD)
    process = None
    log = None
    events = []

    def event(name, **fields):
        events.append(dict(event=name, monotonic_ns=time.monotonic_ns(), **fields))
        save(out / 'events.json', events)

    if out.name == 'pressure':
        revoke = case.revoke

        def with_pressure():
            nonlocal process, log
            event('before_pressure', issuer_pids=[p.pid for p, _ in case.issuer.daemons],
                  fixture_device=case.issuer.state.stat().st_dev, scratch_device=out.stat().st_dev)
            assert case.issuer.state.stat().st_dev == out.stat().st_dev
            log = (out / 'writer-process.log').open('xb')
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--writer', str(out)],
                                       stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 5
            while not (out / 'writer-ready').exists():
                assert process.poll() is None and time.monotonic() < deadline, 'Writer did not become ready'
                time.sleep(.005)
            event('revoke_start', writer_pid=process.pid)
            try:
                return revoke()
            finally:
                event('revoke_return')
        case.revoke = with_pressure

    event('case_start', method=METHOD, pressure_hook=out.name == 'pressure')
    try:
        with (out / 'test.log').open('x') as stream:
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.TestSuite([case]))
        record = dict(passed=result.wasSuccessful(), tests=result.testsRun,
                      failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped))
        save(out / 'result.json', record)
        event('case_end', **record)
        return 0 if result.wasSuccessful() else 1
    finally:
        if process:
            (out / 'writer-stop').touch()
            try:
                code = process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise RuntimeError('Owned writer exceeded its shutdown budget')
            finally:
                log.close()
            event('writer_exit', exit_code=code)
            assert code == 0, 'Bounded writer failed'


def busy():
    found = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            name = (path / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if name in ('cc1', 'cc1plus', 'clang', 'clang++', 'lto1', 'ninja', 'ctest', 'bpftrace'):
            found.append(dict(pid=int(path.name), name=name))
    return found


def run():
    assert socket.gethostname().split('.')[0] == 'sb16'
    lock = (BASE / 'm02-phase-qualification.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not busy(), 'Concurrent build, qualification, or tracing'
    assert shutil.disk_usage(BASE).free > 8 * 1024**3
    ROOT.mkdir(exist_ok=False)
    manifest = json.loads((GATE / 'manifest.json').read_bytes())
    prior = json.loads((GATE / 'summary.json').read_bytes())
    record = dict(status='preparing', host='sb16', started=time.time(), pid=os.getpid(),
                  source_commit=manifest['commit'], runner_sha256=sha(Path(__file__)),
                  manifest_sha256=sha(GATE / 'manifest.json'), profile='runtime-sanitize',
                  selected_method=METHOD, production_changed=False, assertions_changed=False,
                  deadlines_changed=False, durability_changed=False, historical_causes_proved=False,
                  phases=[], monitor_errors=[])
    (ROOT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    save(ROOT / 'review.json', record)

    def audit():
        assert all(sha(SOURCE / name) == digest for name, digest in manifest['files'].items())
        assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
        assert all(sha(BUILD / name) == digest for name, digest in prior['profiles']['runtime-sanitize']['binaries'].items())
        assert sha(Path(prior['server']['path'])) == prior['server']['sha256']
        return dict(source_files=len(manifest['files']), locked_files=len(manifest['locked_files']),
                    binaries=len(prior['profiles']['runtime-sanitize']['binaries']), server=prior['server'])

    stop = threading.Event()

    def sample():
        try:
            with (ROOT / 'resources.jsonl').open('x') as log:
                while not stop.is_set():
                    memory = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                              if line.split(':')[0] in ('MemAvailable', 'Dirty', 'Writeback')}
                    log.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(), memory_kib=memory,
                                             io_pressure=Path('/proc/pressure/io').read_text(),
                                             device_stat=Path('/sys/block/nvme0n1/stat').read_text()))+'\n')
                    log.flush()
                    stop.wait(.25)
        except BaseException as error:
            record['monitor_errors'].append(type(error).__name__ + ': ' + str(error))

    monitor = threading.Thread(target=sample)
    try:
        record['before'] = audit()
        metadata = json.loads(subprocess.check_output(['ctest', '--test-dir', str(BUILD),
                                                       '--show-only=json-v1', '-R', '^authority-supervisor$']))
        assert len(metadata['tests']) == 1
        save(ROOT / 'original-ctest.json', metadata['tests'][0])
        monitor.start()
        for phase in ('baseline', 'pressure', 'recovery'):
            assert not busy()
            out = ROOT / phase
            out.mkdir()
            before = time.monotonic_ns()
            subprocess.run(['sync', '-f', str(ROOT)], check=True, timeout=60)
            sync_ns = time.monotonic_ns() - before
            command = metadata['tests'][0]['command'].copy()
            command = ['--unit=spatial-m02-durability01-' + phase if arg.startswith('--unit=') else
                       '--setenv=SPATIAL_SECURITY_EVIDENCE=' + str(out / 'native')
                       if arg.startswith('--setenv=SPATIAL_SECURITY_EVIDENCE=') else arg for arg in command]
            index = command.index(str(SOURCE / 'tests/native/test_authority_supervisor.py'))
            command[index:index+1] = [str(Path(__file__).resolve()), '--worker', str(out)]
            trace = ['python3', str(BASE / 'trace-qualification-03.py'), '--output-dir', str(out / 'trace'),
                     '--template', str(BASE / 'qualification-syscalls-03.bt'), '--timeout', '120']
            for binary in ('identityd', 'spatial-authority-supervisor', 'spatial-participant-runtime-probe'):
                trace += ['--track-executable', str(BUILD / binary)]
            trace += ['--', *command]
            entry = dict(phase=phase, sync_duration_ns=sync_ns, command=trace, started_ns=time.monotonic_ns())
            record['phases'].append(entry)
            record['status'] = phase
            save(ROOT / 'review.json', record)
            print('starting ' + phase, flush=True)
            with (out / 'command.log').open('xb') as log:
                result = subprocess.run(trace, cwd=SOURCE, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=175, env={k:v for k,v in os.environ.items() if k != 'LSAN_OPTIONS'})
            entry.update(exit_code=result.returncode, ended_ns=time.monotonic_ns())
            entry['trace'] = json.loads((out / 'trace/record.json').read_bytes())
            assert entry['trace']['trace_complete'], 'Incomplete kernel trace'
            entry['result'] = json.loads((out / 'result.json').read_bytes())
            assert entry['result']['tests'] == 1 and entry['result']['skipped'] == 0
            print(phase + ': ' + json.dumps(entry['result']), flush=True)
            save(ROOT / 'review.json', record)
            if phase != 'pressure':
                assert result.returncode == 0, 'Unpressured control failed; reassess before more pressure'
        record['after'] = audit()
        assert not record['monitor_errors'] and not busy()
        record['status'] = 'diagnostic_complete_review_pending'
    except BaseException as error:
        record.update(status='diagnostic_error', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        stop.set()
        if monitor.is_alive():
            monitor.join(3)
        record['ended'] = time.time()
        record['attachments'] = {p.relative_to(ROOT).as_posix(): sha(p) for p in ROOT.rglob('*')
                                 if p.is_file() and p.name not in ('scratch.bin', 'review.json')}
        save(ROOT / 'review.json', record)
        print(record['status'], flush=True)


if __name__ == '__main__':
    assert socket.gethostname().split('.')[0] == 'sb16'
    if len(sys.argv) > 1 and sys.argv[1] in ('--writer', '--worker'):
        mode = sys.argv.pop(1)
        directory = Path(sys.argv.pop(1))
        assert directory.parent == ROOT and directory.name in ('baseline', 'pressure', 'recovery')
        if mode == '--writer':
            writer(directory)
        else:
            raise SystemExit(worker(directory))
    else:
        assert len(sys.argv) == 1
        run()
