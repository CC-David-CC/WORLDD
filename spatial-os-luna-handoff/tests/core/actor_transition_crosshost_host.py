#!/usr/bin/env python3
"""Trusted bounded worldd orchestration. Never implements semantic actor actions.

Management frames reach worldd unchanged. The private Unix socket retains its
management pipe across SSH disconnects. tmux retains the bounded owner process.
All mutation is confined to a newly created marker/inode-checked fixture root.
"""
import argparse
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import select
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

import core_pb2 as core
from harness.wire import frame, make_request

LIMIT = 1048576
FILES = {'issuer.textproto', 'identity.textproto', 'participant.textproto',
         'seed.textproto', 'runtime.textproto', 'tls.textproto', 'service.pem',
         'service.crt', 'server.pem', 'server.crt', 'issuer.crt', 'participant.pem'}


def atomic_json(path, value):
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)


def identity(pid):
    path = Path('/proc') / str(pid)
    parts = (path / 'stat').read_text().rsplit(') ', 1)[1].split()
    return dict(pid=pid, start_ticks=int(parts[19]),
                executable_sha256=hashlib.sha256((path / 'exe').read_bytes()).hexdigest(),
                command=[os.fsdecode(x) for x in (path / 'cmdline').read_bytes().split(b'\0') if x])


def proc_start_ticks(pid):
    """Read the process start tick without requiring executable or cmdline access."""
    path = Path('/proc') / str(pid)
    try:
        fields = (path / 'stat').read_text().rsplit(') ', 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return int(fields[19])


def pidfd_proves_exit(fd, record):
    """Require kernel pidfd readiness and reject a live PID-reuse mismatch."""
    poller = select.poll()
    poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    events = poller.poll(0)
    if not events:
        return False
    readiness = events[0][1]
    if readiness & (select.POLLERR | select.POLLNVAL):
        return False
    if not readiness & (select.POLLIN | select.POLLHUP):
        return False
    start_ticks = proc_start_ticks(record['pid'])
    if start_ticks is None:
        return True
    return start_ticks == record['start_ticks']


def guarded_pidfd(record):
    fd = os.pidfd_open(record['pid'])
    try:
        try:
            current = identity(record['pid'])
        except (FileNotFoundError, ProcessLookupError) as error:
            if pidfd_proves_exit(fd, record):
                raise ProcessLookupError('Recorded process exited during identity check') from error
            raise RuntimeError('Could not verify recorded process identity') from error
        if current != record:
            if pidfd_proves_exit(fd, record):
                raise ProcessLookupError('Recorded process exited during identity check')
            raise RuntimeError('PID/start/executable/command ownership mismatch')
        return fd
    except BaseException:
        os.close(fd)
        raise


def root_guard(root, token):
    root = Path(root)
    if root.parent != Path('/tmp') or not root.name.startswith('spatial-m06-run03-'):
        raise ValueError('Root is outside the fixture namespace')
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Root ownership/mode mismatch')
    marker_path = root / 'owner.json'
    if marker_path.is_symlink():
        raise ValueError('Marker is a symlink')
    marker = json.loads(marker_path.read_text())
    if marker != dict(token=token, device=info.st_dev, inode=info.st_ino, uid=os.getuid()):
        raise ValueError('Marker or directory inode changed')
    return root


def exact_fd(fd, count, deadline):
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError('Management frame deadline')
        part = os.read(fd, count - len(result))
        if not part:
            raise EOFError('Management channel ended')
        result.extend(part)
    return bytes(result)


def read_frame_fd(fd, timeout=10):
    deadline = time.monotonic() + timeout
    header = exact_fd(fd, 4, deadline)
    size = struct.unpack('!I', header)[0]
    if not 0 < size <= LIMIT:
        raise ValueError('Invalid management frame length')
    return header + exact_fd(fd, size, deadline)


def logged_run(root, name, command, payload=b''):
    atomic_json(root / (name + '-command.json'), dict(argv=command))
    try:
        result = subprocess.run(command, input=payload, capture_output=True, timeout=15)
    except subprocess.TimeoutExpired as error:
        (root / (name + '.stderr')).write_bytes(error.stderr or b'')
        (root / (name + '.stdout.pb')).write_bytes(error.stdout or b'')
        (root / (name + '.exit')).write_text('TIMEOUT\n')
        raise
    (root / (name + '.stderr')).write_bytes(result.stderr)
    (root / (name + '.stdout.pb')).write_bytes(result.stdout)
    (root / (name + '.exit')).write_text(str(result.returncode) + '\n')
    if result.returncode:
        raise RuntimeError(name + ' failed; inspect retained command logs')
    return result.stdout


def stop_child(proc, record):
    if proc.poll() is not None:
        return proc.returncode
    try:
        fd = guarded_pidfd(record)
    except (FileNotFoundError, ProcessLookupError):
        return proc.wait(timeout=3)  # The retained child exited during the witness.
    try:
        proc.stdin.close()  # Production management EOF requests orderly shutdown.
        try:
            return proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            try:
                return proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                return proc.wait(timeout=3)
    finally:
        os.close(fd)


def serve(root, token, binary, address):
    root_guard(root, token)
    listener = socket.socket()
    management = socket.socket(socket.AF_UNIX)
    stop = socket.socket(socket.AF_UNIX)
    proc = None
    unavailable = None
    record = None
    log = None
    shutdown = False
    def cancelled(*_):
        nonlocal shutdown
        shutdown = True
    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGINT, cancelled)
    try:
        listener.bind((address, 0))
        listener.listen(16)
        # Reserve an unlistening endpoint for the outage cases for this entire run.
        unavailable = socket.socket()
        unavailable.bind((address, 0))
        management.bind(str(root / 'management.sock'))
        management.listen(4)
        stop.bind(str(root / 'stop.sock'))
        stop.listen(1)
        if (root / 'cancel').exists():
            return
        command = [str(binary), '--state-dir', str(root / 'state'), '--config', str(root / 'runtime.textproto'),
                   '--identity-config', str(root / 'identity.textproto'), '--actor-listen-fd', str(listener.fileno()),
                   '--actor-tls-config', str(root / 'tls.textproto')]
        atomic_json(root / 'world-command.json', dict(argv=command))
        log = (root / 'worldd.stderr').open('wb')
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=log, pass_fds=(listener.fileno(),))
        record = identity(proc.pid)
        atomic_json(root / 'process.json', dict(worldd=record, owner=identity(os.getpid()),
                    port=listener.getsockname()[1], unavailable_port=unavailable.getsockname()[1],
                    state=str(root / 'state'), database=str(root / 'state/world.db'), address=address))
        listener.close()
        deadline = time.monotonic() + 1200
        while not shutdown and time.monotonic() < deadline and proc.poll() is None:
            ready, _, _ = select.select([management, stop], [], [], .2)
            if stop in ready or (root / 'cancel').exists():
                shutdown = True
                break
            if management not in ready:
                continue
            connection, _ = management.accept()
            with connection:
                request = read_frame_fd(connection.fileno())
                parsed = core.Request.FromString(request[4:])
                if parsed.protocol_version != 1 or parsed.WhichOneof('operation') not in (
                        'hello', 'world_info', 'world_create', 'world_checkpoint'):
                    raise ValueError('Fixture management operation outside setup scope')
                proc.stdin.write(request)
                proc.stdin.flush()
                reply = read_frame_fd(proc.stdout.fileno())
                with (root / 'management.pb.log').open('ab') as audit:
                    audit.write(request + reply)
                connection.sendall(reply)
    finally:
        if proc is not None:
            # Retain independent durable state/log evidence before any termination.
            try:
                snapshot(root, token, output=root / 'before-stop.tar')
            finally:
                result = stop_child(proc, record)
                (root / 'worldd.exit').write_text(str(result) + '\n')
                proc.stdout.close()
        if log:
            log.close()
        listener.close()
        management.close()
        stop.close()
        if unavailable:
            unavailable.close()



def stop_owner_backlog_regression(evidence):
    """Prove a slow owner receives one stop request, not a backlog of retries."""
    import shutil
    token = __import__('uuid').uuid4().hex
    root = Path(tempfile.mkdtemp(prefix='spatial-m06-run03-' + token + '-', dir='/tmp'))
    root.chmod(0o700)
    marker = root.stat()
    atomic_json(root / 'owner.json', dict(token=token, device=marker.st_dev, inode=marker.st_ino, uid=os.getuid()))
    stop_path = root / 'stop.sock'
    ready = threading.Event()
    received = []
    server_errors = []

    def slow_owner():
        listener = socket.socket(socket.AF_UNIX)
        try:
            listener.bind(str(stop_path))
            listener.listen(1)
            ready.set()
            connection, _ = listener.accept()
            with connection:
                received.append(connection.recv(1))
            time.sleep(.7)  # Keep the recorded owner live while shutdown is pending.
            listener.settimeout(.05)
            while True:
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    break
                with connection:
                    received.append(connection.recv(1))
        except BaseException as error:
            server_errors.append(repr(error))
            ready.set()
        finally:
            listener.close()

    thread = threading.Thread(target=slow_owner, name='m06-stop-owner-regression', daemon=True)
    child = None
    original_run = subprocess.run
    session_checks = {'count': 0}
    try:
        thread.start()
        if not ready.wait(3):
            raise TimeoutError('Synthetic stop owner did not start')
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        dead_world = identity(child.pid)
        child.terminate()
        child.wait(timeout=3)
        atomic_json(root / 'launch.json', dict(session='m06-stop-owner-regression'))
        atomic_json(root / 'process.json', dict(owner=identity(os.getpid()), worldd=dead_world))

        def fake_tmux_run(command, *args, **kwargs):
            if command and command[0] == 'tmux':
                session_checks['count'] += 1
                code = 0 if session_checks['count'] <= 8 else 1
                return subprocess.CompletedProcess(command, code, b'', b'')
            return original_run(command, *args, **kwargs)

        subprocess.run = fake_tmux_run
        try:
            stop_owner(root, token)
        finally:
            subprocess.run = original_run
        thread.join(timeout=3)
        if thread.is_alive():
            raise TimeoutError('Synthetic slow owner did not finish')
        if server_errors:
            raise AssertionError('Synthetic stop owner failed: ' + '; '.join(server_errors))
        if received != [b'S']:
            raise AssertionError(f'Expected one stop byte; observed {received!r}')
        if session_checks['count'] < 2:
            raise AssertionError('Stop helper did not poll the retained owner process')
        report = ('PASS: retained owner PID was polled while a slow Unix listener drained; '
                  'exactly one stop byte was queued; EAGAIN did not trigger repeated requests.\n')
        if evidence is not None:
            (Path(evidence) / 'cleanup-transport-regression.txt').write_text(report, encoding='utf-8')
        print(report.strip(), flush=True)
    finally:
        subprocess.run = original_run
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        thread.join(timeout=3)
        if not thread.is_alive():
            root_guard(root, token)
            shutil.rmtree(root)


def snapshot(root, token, output=None):
    root_guard(root, token)
    # SQLite online backup reads real databases; no writes to authoritative state.
    temporary = root / 'snapshot-world.db'
    database = root / 'state/world.db'
    if database.exists():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=3) as source:
            with sqlite3.connect(temporary) as destination:
                deadline = time.monotonic() + 8
                def progress(*_):
                    if time.monotonic() > deadline:
                        raise TimeoutError('Durable backup deadline')
                source.backup(destination, pages=128, sleep=.01, progress=progress)
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        for item in root.iterdir():
            if item.is_symlink():
                raise ValueError('Symlink in fixture snapshot')
            if item.is_file() and (item.suffix in ('.json', '.stderr', '.exit') or
                                  item.name in ('snapshot-world.db', 'management.pb.log', 'before-stop.tar')):
                archive.add(item, arcname=item.name, recursive=False)
        authority = root / 'authority'
        if authority.exists():
            for item in authority.iterdir():
                if item.is_symlink():
                    raise ValueError('Symlink in authority snapshot')
                if item.is_file() and item.suffix == '.db':
                    backup = root / ('snapshot-' + item.name)
                    with sqlite3.connect(item.as_uri() + '?mode=ro', uri=True) as source:
                        with sqlite3.connect(backup) as destination:
                            source.backup(destination)
                    archive.add(backup, arcname='authority/' + item.name)
    if output:
        output.write_bytes(data.getvalue())
    else:
        sys.stdout.buffer.write(data.getvalue())


def request_stop_once(path, timeout):
    """Send one bounded stop byte; never retry an ambiguous send."""
    channel = socket.socket(socket.AF_UNIX)
    channel.setblocking(False)
    deadline = time.monotonic() + timeout
    try:
        try:
            channel.connect(str(path))
        except BlockingIOError as error:
            if error.errno not in (errno.EINPROGRESS, errno.EALREADY, errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                _, writable, exceptional = select.select([], [channel], [channel], remaining)
                if not writable and not exceptional:
                    return False
                status = channel.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if status == 0:
                    break
                if status not in (errno.EINPROGRESS, errno.EALREADY, errno.EAGAIN, errno.EWOULDBLOCK):
                    return False
                time.sleep(min(0.01, remaining))
        try:
            return channel.send(b'S') == 1
        except BlockingIOError:
            return False
    finally:
        channel.close()


def stop_owner(root, token):
    root_guard(root, token)
    (root / 'cancel').touch(mode=0o600, exist_ok=True)
    process_path = root / 'process.json'
    deadline = time.monotonic() + 20
    stop_attempted = False
    # start records its tmux intent before the job can spawn a worldd.
    while (root / 'launch.json').exists():
        launch = json.loads((root / 'launch.json').read_text())
        alive = subprocess.run(['tmux', 'has-session', '-t', '=' + launch['session']],
                               capture_output=True, timeout=3).returncode == 0
        if not alive:
            break
        if process_path.exists():
            record = json.loads(process_path.read_text())
            try:
                fd = guarded_pidfd(record['owner'])
            except (FileNotFoundError, ProcessLookupError):
                fd = None
            if fd is not None:
                os.close(fd)
                if not stop_attempted:
                    stop_attempted = True
                    try:
                        request_stop_once(root / 'stop.sock', min(1.0, max(0.0, deadline - time.monotonic())))
                    except (FileNotFoundError, ConnectionRefusedError, TimeoutError):
                        pass  # The persistent cancel marker still wakes the bounded owner loop.
        if time.monotonic() >= deadline:
            raise RuntimeError('Owner still running; retain root and recover with stop command')
        time.sleep(.1)
    if process_path.exists():
        record = json.loads(process_path.read_text())
        try:
            fd = guarded_pidfd(record['worldd'])
        except (FileNotFoundError, ProcessLookupError):
            return
        else:
            os.close(fd)
            raise RuntimeError('Recorded worldd still exists; refuse path removal')
    else:
        issuer_record = root / 'issuer-process.json'
        if issuer_record.exists():
            record = json.loads(issuer_record.read_text())['process']
            try:
                fd = guarded_pidfd(record)
            except (FileNotFoundError, ProcessLookupError):
                pass
            else:
                os.close(fd)
                raise RuntimeError('Owned issuer still exists; refuse path removal')
        # A launch can fail before its PID record reaches disk. Retain any such
        # unrecorded process rather than deleting its live state directory.
        expected = {os.fsencode(str(root / 'state')), os.fsencode(str(root / 'issuer'))}
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                command = (proc / 'cmdline').read_bytes().split(b'\0')
                if b'--state-dir' in command and command[command.index(b'--state-dir') + 1] in expected:
                    raise RuntimeError('Unrecorded process still references fixture state; retain root')
            except (OSError, IndexError):
                continue


def remove(root, token):
    root_guard(root, token)
    stop_owner(root, token)
    # Never follow links. Reject mounted directories or another user's contents.
    device = root.stat().st_dev
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            item = Path(directory) / name
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_dev != device or info.st_uid != os.getuid():
                raise ValueError('Unexpected ownership/link/mount; retain fixture root')
    import shutil
    shutil.rmtree(root)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('create', 'put', 'initialize', 'authority', 'start',
                                              'serve', 'management', 'status', 'snapshot', 'stop', 'remove'))
    parser.add_argument('token')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--address')
    parser.add_argument('--name')
    args = parser.parse_args()
    os.umask(0o077)
    if socket.gethostname().split('.')[0] not in ('sb16', 'sb16-2'):
        raise RuntimeError('Runtime helper forbidden outside qualification hosts')
    if len(args.token) != 32 or any(c not in '0123456789abcdef' for c in args.token):
        raise ValueError('Invalid ownership token')
    if args.operation == 'create':
        root = args.root
        if root is None or root.parent != Path('/tmp') or root.name != 'spatial-m06-run03-' + args.token + '-' + socket.gethostname().split('.')[0]:
            raise ValueError('Create requires the exact recorded token/host path')
        root.mkdir(mode=0o700, exist_ok=False)
        info = root.stat()
        atomic_json(root / 'owner.json', dict(token=args.token, device=info.st_dev, inode=info.st_ino, uid=os.getuid()))
        print(root)
        return
    root = root_guard(args.root, args.token)
    if args.operation == 'put':
        if args.name not in FILES:
            raise ValueError('Unknown fixture material name')
        payload = sys.stdin.buffer.read(LIMIT + 1)
        if not payload or len(payload) > LIMIT:
            raise ValueError('Fixture material size outside bound')
        with (root / args.name).open('xb') as output:
            output.write(payload)
    elif args.operation == 'initialize':
        payload = frame(make_request('world.info', {})) + frame(make_request('world.create',
                   {'entity': {'id': 'saved-room', 'kind': 'room', 'label': 'run03 saved context'}}))
        sys.stdout.buffer.write(logged_run(root, 'initialize', [str(args.binary), '--state-dir', str(root / 'state')], payload))
    elif args.operation == 'authority':
        (root / 'authority').mkdir(mode=0o700, exist_ok=False)
        logged_run(root, 'authority', [str(args.binary), '--state-dir', str(root / 'state'),
                   '--init-authority', str(root / 'seed.textproto')])
    elif args.operation == 'start':
        session = 'm06-run03-' + args.token + '-' + root.name.rsplit('-', 1)[-1]
        inherited = ['env'] + [name + '=' + os.environ[name] for name in
            ('PYTHONPATH', 'LD_LIBRARY_PATH', 'PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION', 'ASAN_OPTIONS', 'UBSAN_OPTIONS')
            if name in os.environ]
        command = inherited + [sys.executable, str(Path(__file__).resolve()), 'serve', args.token,
                   '--root', str(root), '--binary', str(args.binary), '--address', args.address]
        import shlex
        atomic_json(root / 'launch.json', dict(session=session, argv=command))
        shell_command = shlex.join(command) + ' >' + shlex.quote(str(root / 'owner.stdout')) + ' 2>' + shlex.quote(str(root / 'owner.stderr'))
        subprocess.run(['tmux', 'new-session', '-d', '-s', session, shell_command], check=True, timeout=5)
        deadline = time.monotonic() + 15
        while not (root / 'process.json').exists():
            if time.monotonic() > deadline:
                raise TimeoutError('World owner did not write process record')
            time.sleep(.05)
        print((root / 'process.json').read_text())
    elif args.operation == 'serve':
        serve(root, args.token, args.binary, args.address)
    elif args.operation == 'management':
        request = read_frame_fd(sys.stdin.fileno())
        with socket.socket(socket.AF_UNIX) as channel:
            channel.settimeout(12)
            channel.connect(str(root / 'management.sock'))
            channel.sendall(request)
            sys.stdout.buffer.write(read_frame_fd(channel.fileno()))
    elif args.operation == 'status':
        print((root / 'process.json').read_text())
    elif args.operation == 'snapshot':
        snapshot(root, args.token)
    elif args.operation == 'stop':
        stop_owner(root, args.token)
    elif args.operation == 'remove':
        remove(root, args.token)


if __name__ == '__main__':
    main()
