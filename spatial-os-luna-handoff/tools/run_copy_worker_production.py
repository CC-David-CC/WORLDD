"""Run the privileged copy fixture from its nonroot CTest entry point."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

MODES = {'readonly-destination', 'writable-source', 'receipt-control',
         'receipt-binding', 'success', 'symlink'}
SANITIZER_MARKERS = ('AddressSanitizer', 'LeakSanitizer',
                     'UndefinedBehaviorSanitizer', 'runtime error:')


def validate_receipt(record):
    assert not [key for key in record if key.startswith('cleanup_') or key == 'helper_restore_error']
    assert not any(marker in json.dumps(record) for marker in SANITIZER_MARKERS)
    assert record['finished'] > 0 and 'cancelled_signal' not in record
    assert record['volume_cleanup']['applied'] is True
    assert record['volume_cleanup']['status'] == 'unmounted'
    assert record['volume_cleanup']['mounted'] is False
    assert record['volume_cleanup']['loop'] is None
    scopes = record['case_scopes']
    assert len(scopes) == 6 and {case['mode'] for case in scopes} == MODES
    assert len({case['scope'] for case in scopes}) == 6
    assert len({case['resource_config'] for case in scopes}) == 6
    for case in scopes:
        scope = case['delegation']
        assert scope['exit'] == 0 and scope['verified']['uid'] == pwd.getpwnam('dflanag3').pw_uid > 0
        assert scope['verified']['scope'] == case['scope']
        assert all(item['uid'] == scope['verified']['uid'] and item['writable_as_uid']
                   for item in scope['verified']['files'].values())
        assert set(scope['verified']['files']) == {'cgroup.procs', 'cgroup.subtree_control', 'cgroup.threads'}
        assert scope['verified']['initial_limits'] == {'memory.max':'max\n','memory.swap.max':'max\n',
               'pids.max':'max\n','cpu.max':'max 100000\n','io.max':''}
        assert case['removed'] and case['empty_before_removal']
    assert record['scope_removed'] and record['socket_directory_removed']
    assert record['owned_scopes_empty_before_unit_stop'] is True
    assert record['unit_stop']['stop_exit'] == 0
    assert record['unit_stop']['state'] in ('inactive', 'unknown')
    assert record['unit_stop']['is_active_exit'] in (3, 4)
    recovery = record['restart_recovery']
    assert recovery['uid'] == pwd.getpwnam('dflanag3').pw_uid > 0 and recovery['exit'] == 0
    assert 'Ran 1 test' in recovery['stderr'] and 'OK' in recovery['stderr']
    scans=record['restart_owner_stderr_scans']
    assert len(scans)==2 and {Path(scan['path']).name for scan in scans}=={'owner-1.stderr.log','owner-2.stderr.log'}
    assert all(scan['sanitizer_free'] and 0<=scan['bytes']<=8*2**20 and len(scan['sha256'])==64 for scan in scans)
    assert 'MALFORMED_OWNER_FD_STABLE 32' in record['malformed_owner_fd_regression']
    witnesses = record['witnesses']
    assert len(witnesses) == 6 and {item['mode'] for item in witnesses} == MODES
    for item in witnesses:
        assert item['exit'] == 0
        mode = item['mode']
        case = next(case for case in scopes if case['mode'] == mode)
        assert item['broker_scope'] == case['scope']
        assert all(Path(sample['worker_cgroup']).is_relative_to(Path(case['scope'])) for sample in item['scopes'])
        if mode in ('receipt-control', 'receipt-binding'):
            relay = item['relay']
            assert relay['peer_uid'] == 0
            assert relay['broker_uid'] == pwd.getpwnam('dflanag3').pw_uid > 0
            assert relay['broker_exit'] == 0 and relay['owner_exit'] == 0 and relay['client_eof']
            assert relay['requests'].count('copy_stage_begin') >= 1
            if mode == 'receipt-control':
                assert not relay['mutations']
                assert relay['requests'].count('copy_stage_start') == 1
                assert relay['requests'].count('copy_stage_seal') == 1
            else:
                assert len(relay['mutations']) == 1
                assert relay['mutations'][0]['field'] == 'destination_subvolume_id'
                assert relay['requests'].count('copy_stage_begin') == 1
                assert relay['requests'][-1] == 'copy_stage_begin'
                assert not any(method in relay['requests'] for method in
                               ('copy_stage_start', 'copy_stage_seal', 'copy_stage_uncertain', 'copy_stage_inspect'))
                assert 'BEGIN_BINDING_REJECTED UNAVAILABLE exact-cause channel=fenced destination=unchanged worker=unprepared' in item['stdout']
                assert not item['scopes']
        if mode in ('success', 'receipt-control'):
            assert 'retry=identical inode=independent' in item['stdout']
            assert any(sample.get('memory.max') == str(256 * 2**20) and sample.get('pids.max') == '16'
                       and sample.get('cpu.max') == '200000 100000' and sample.get('private_netns')
                       and sample.get('worker_pid') and sample.get('worker_cgroup') for sample in item['scopes'])
        if mode == 'symlink':
            assert 'SYMLINK_REJECTED UNCERTAIN' in item['stdout']
        if mode == 'readonly-destination':
            assert 'READONLY_DESTINATION_REJECTED exact-cause destination=unchanged worker=unprepared channel=alive' in item['stdout']
            assert not item['scopes']
        if mode == 'writable-source':
            assert 'WRITABLE_SOURCE_REJECTED exact-cause destination=unchanged worker=unprepared false-readonly=fenced' in item['stdout']
            assert not item['scopes']


def helper_state():
    path = Path('/usr/local/libexec/spatial-copy-tree-worker')
    if not os.path.lexists(path):
        return {'exists': False}
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_uid == 0
    return {'exists': True, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}



def cancel_owned_fixture(control, build, source, evidence, signum):
    assert os.getuid() == 0
    operator = pwd.getpwnam('dflanag3')
    assert control == evidence.parent / 'copy-fixture-process.json'
    assert evidence.parent.parent == Path(operator.pw_dir) / 'spatial-artifacts/runs'
    assert evidence.parent.name.startswith('copy-worker-production-')
    assert not control.is_symlink() and control.stat().st_uid == operator.pw_uid
    process = json.loads(control.read_text())
    pid = process['pid']
    assert isinstance(pid, int) and pid > 1
    expected = [sys.executable, '-B', str(source / 'tests/native/test_copy_worker_production.py'),
                str(build), str(source), str(evidence)]
    assert process['argv'] == expected
    proc = Path('/proc') / str(pid)
    descriptor = None
    try:
        descriptor = os.pidfd_open(pid)
        assert (proc / 'status').read_text().split('Uid:', 1)[1].splitlines()[0].split()[1] == '0'
        assert (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19] == process['start_ticks']
        assert (proc / 'cmdline').read_bytes().rstrip(b'\0').decode().split('\0') == expected
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        pass
    except FileNotFoundError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def wait_for_fixture(command, parent, build, source, evidence):
    cancelled = []
    previous = {}
    def request(signum, _frame):
        cancelled.append(signum)
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, request)
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 600
    cancellation = None
    try:
        while True:
            try:
                stdout, stderr = child.communicate(timeout=1)
                return child.returncode, stdout, stderr, cancellation
            except subprocess.TimeoutExpired:
                if not cancelled and time.monotonic() < deadline:
                    continue
                reason = 'signal' if cancelled else '600-second execution deadline'
                cancellation = {'reason': reason, 'grace_seconds': 240}
                control = parent / 'copy-fixture-process.json'
                cancel_command = ['sudo', '-n', sys.executable, '-B', str(Path(__file__).resolve()),
                                  '--cancel', str(control), str(build), str(source), str(evidence)]
                if control.exists():
                    result = subprocess.run(cancel_command, capture_output=True, text=True, timeout=10)
                    cancellation.update(signal_exit=result.returncode, signal_stdout=result.stdout,
                                        signal_stderr=result.stderr)
                    cancellation['recovery_command'] = cancel_command
                else:
                    # The root fixture writes control before any privileged project effects.
                    child.terminate()
                    cancellation['no_fixture_control'] = True
                try:
                    stdout, stderr = child.communicate(timeout=240)
                except subprocess.TimeoutExpired:
                    result = subprocess.run(cancel_command + ['--kill'], capture_output=True, text=True, timeout=10)
                    cancellation.update(forced_kill_exit=result.returncode, forced_kill_stdout=result.stdout,
                                        forced_kill_stderr=result.stderr, cleanup_incomplete=True,
                                        recovery_command=cancel_command)
                    try:
                        stdout, stderr = child.communicate(timeout=10)
                    except subprocess.TimeoutExpired as error:
                        cancellation['root_exit_unconfirmed'] = True
                        stdout = error.stdout or ''
                        stderr = error.stderr or ''
                        if isinstance(stdout, bytes): stdout = stdout.decode(errors='replace')
                        if isinstance(stderr, bytes): stderr = stderr.decode(errors='replace')
                        return 124, stdout, stderr, cancellation
                return child.returncode, stdout, stderr, cancellation
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main():
    operator = pwd.getpwnam('dflanag3')
    assert os.getuid() == operator.pw_uid > 0
    assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
    build, source = (Path(arg).resolve(strict=True) for arg in sys.argv[1:3])
    assert len(sys.argv) == 3 and build.is_dir() and source.is_dir()
    runs = Path(operator.pw_dir) / 'spatial-artifacts/runs'
    runs.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert not runs.is_symlink() and runs.resolve() == runs and runs.stat().st_uid == operator.pw_uid
    parent = Path(tempfile.mkdtemp(prefix='copy-worker-production-', dir=runs))
    assert parent.stat().st_uid == operator.pw_uid and stat.S_IMODE(parent.stat().st_mode) == 0o700
    evidence = parent / 'production-evidence'
    before = helper_state()
    command = ['sudo', '-n', 'env', '-u', 'LSAN_OPTIONS', 'PYTHONDONTWRITEBYTECODE=1',
               'PYTHONPATH=' + str(build / 'python') + ':' + str(source),
               'ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
               'UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
               sys.executable, '-B', str(source / 'tests/native/test_copy_worker_production.py'),
               str(build), str(source), str(evidence)]
    (parent / 'launch.json').write_text(json.dumps({'argv': command, 'operator_uid': operator.pw_uid,
                                                  'parent_mode': '0700', 'evidence': str(evidence)}, indent=2) + '\n')
    print('COPY_WORKER_PRODUCTION_EVIDENCE=' + str(parent), flush=True)
    exit_code, stdout, stderr, cancellation = wait_for_fixture(command, parent, build, source, evidence)
    (parent / 'stdout.log').write_text(stdout)
    (parent / 'stderr.log').write_text(stderr)
    (parent / 'command.exit').write_text(str(exit_code) + '\n')
    after = helper_state()
    summary = {'command_exit': exit_code, 'cancellation': cancellation, 'helper_before': before, 'helper_after': after,
               'helper_restored': before == after, 'validated': False}
    try:
        assert not any(marker in stdout + stderr for marker in SANITIZER_MARKERS)
        assert before == after
        prefix = 'SPATIAL_COPY_PRODUCTION_RECEIPT '
        receipts = [line[len(prefix):] for line in stdout.splitlines() if line.startswith(prefix)]
        assert len(receipts) == 1, 'missing or ambiguous root fixture receipt'
        record = json.loads(receipts[0])
        if exit_code != 0:
            raise RuntimeError('Privileged copy fixture failed (exit=' + str(exit_code) +
                               '); retained diagnostics: ' + str(parent / 'stderr.log'))
        validate_receipt(record)
        assert exit_code == 0 and cancellation is None
        summary.update(validated=True, production_witnesses=6, malformed_owner_fd_checks=1, restart_recovery_checks=1)
    finally:
        (parent / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--cancel']:
        assert len(sys.argv) in (6, 7)
        assert len(sys.argv) == 6 or sys.argv[6] == '--kill'
        control, build, source, evidence = (Path(arg).resolve() for arg in sys.argv[2:6])
        cancel_owned_fixture(control, build, source, evidence,
                             signal.SIGKILL if sys.argv[-1:] == ['--kill'] else signal.SIGTERM)
    else:
        main()
