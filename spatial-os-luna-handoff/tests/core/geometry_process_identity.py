"""Narrow protobuf process-identity witness for native geometry fixtures."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import sys
import time

PROC = Path('/proc')
MAX_FRAME = 1024 * 1024
MAX_PROCESSES = 16

class ProcessIdentityError(RuntimeError):
    pass

@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    executable_sha256: bytes

@dataclass(frozen=True)
class ProcessFdMatches:
    pid: int
    start_ticks: int
    matched_needles: tuple[str, ...]

def start_ticks(pid, proc=PROC):
    value = (Path(proc) / str(pid) / 'stat').read_text()
    fields = value[value.rfind(')') + 2:].split()
    if len(fields) <= 19:
        raise ProcessIdentityError('Truncated proc stat')
    return int(fields[19])

def pidfd_ready(fd):
    poller = select.poll()
    poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    return bool(poller.poll(0))

def _same_inode(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

def read_identity(pid, expected_ticks, *, proc=PROC, pin=os.pidfd_open,
                  ready=pidfd_ready, read_start=start_ticks, stat=os.stat, open_file=open):
    if type(pid) is not int or pid <= 1 or type(expected_ticks) is not int or expected_ticks <= 0:
        raise ProcessIdentityError('Invalid process identity')
    fd = pin(pid, 0)
    try:
        if ready(fd):
            raise ProcessIdentityError('Process already exited')
        before = read_start(pid, proc)
        if before != expected_ticks:
            raise ProcessIdentityError('Process start identity changed before executable read')
        exe = Path(proc) / str(pid) / 'exe'
        before_file = stat(exe)
        digest = hashlib.sha256()
        with open_file(exe, 'rb') as image:
            opened_file = os.fstat(image.fileno())
            if not _same_inode(before_file, opened_file):
                raise ProcessIdentityError('Executable changed before hashing')
            for block in iter(lambda: image.read(1024 * 1024), b''):
                digest.update(block)
        after_file = stat(exe)
        after = read_start(pid, proc)
        if after != before or after != expected_ticks:
            raise ProcessIdentityError('Process start identity changed during executable read')
        if not _same_inode(opened_file, after_file):
            raise ProcessIdentityError('Executable changed during hashing')
        if ready(fd):
            raise ProcessIdentityError('Process exited during executable read')
        return ProcessIdentity(pid, after, digest.digest())
    finally:
        os.close(fd)

def validate_expected(value):
    if not isinstance(value, list) or not value or len(value) > MAX_PROCESSES:
        raise ValueError('Expected a bounded nonempty process identity list')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError('Invalid process identity pair')
        pid, ticks = item
        if (type(pid) is not int or not 1 < pid <= 2**31-1 or
                type(ticks) is not int or not 0 < ticks <= 2**64-1):
            raise ValueError('Invalid process identity pair')
        if pid in seen:
            raise ValueError('Duplicate PID')
        seen.add(pid); result.append((pid, ticks))
    return result

def _no_unknown_or_noncanonical(message, payload):
    canonical = message.SerializeToString(deterministic=True)
    message.DiscardUnknownFields()
    if message.SerializeToString(deterministic=True) != canonical or canonical != payload:
        raise ProcessIdentityError('Unknown or noncanonical protobuf fields')

def validate_fd_queries(value):
    if not isinstance(value, list) or not value or len(value) > MAX_PROCESSES:
        raise ValueError('Expected a bounded nonempty process descriptor query list')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise ValueError('Invalid process descriptor query')
        pid, ticks, needles = item
        if (type(pid) is not int or not 1 < pid <= 2**31-1 or
                type(ticks) is not int or not 0 < ticks <= 2**64-1):
            raise ValueError('Invalid process descriptor identity')
        if pid in seen:
            raise ValueError('Duplicate process descriptor PID')
        seen.add(pid)
        if not isinstance(needles, (tuple, list)) or not needles or len(needles) > 64:
            raise ValueError('Invalid process descriptor target list')
        checked, target_seen = [], set()
        for needle in needles:
            if (not isinstance(needle, str) or not needle or len(needle.encode()) > 256 or
                    '\0' in needle or needle in target_seen):
                raise ValueError('Invalid process descriptor target')
            target_seen.add(needle); checked.append(needle)
        result.append((pid, ticks, tuple(checked)))
    return result

def parse_fd_scan_request(body, protocol):
    request = protocol.NativeGeometryWitnessRequest()
    try:
        request.ParseFromString(body)
    except Exception as error:
        raise ProcessIdentityError('Malformed root descriptor request') from error
    _no_unknown_or_noncanonical(request, body)
    if request.request_id != 1 or request.WhichOneof('operation') != 'fd_scan':
        raise ProcessIdentityError('Unexpected root descriptor request context')
    scan = request.fd_scan
    if scan.wait_until_absent_ms > 2000:
        raise ProcessIdentityError('Descriptor wait exceeds its bounded deadline')
    queries = [(item.pid, item.start_ticks, list(item.target_needles)) for item in scan.processes]
    try:
        checked = validate_fd_queries(queries)
    except ValueError as error:
        raise ProcessIdentityError(str(error)) from error
    if not checked:
        raise ProcessIdentityError('Descriptor request has no process queries')
    return request, checked, scan.wait_until_absent_ms

def read_fd_matches(pid, expected_ticks, needles, *, wait_ms=0, proc=PROC, pin=os.pidfd_open,
                    ready=pidfd_ready, read_start=start_ticks, listdir=os.listdir,
                    readlink=os.readlink, sleep=time.sleep, monotonic=time.monotonic):
    if (type(pid) is not int or pid <= 1 or type(expected_ticks) is not int or
            expected_ticks <= 0):
        raise ProcessIdentityError('Invalid process descriptor identity')
    if not isinstance(needles, (tuple, list)) or not needles:
        raise ProcessIdentityError('Invalid process descriptor target list')
    descriptor = pin(pid, 0)
    try:
        if ready(descriptor):
            raise ProcessIdentityError('Process already exited')
        before = read_start(pid, proc)
        if before != expected_ticks:
            raise ProcessIdentityError('Process start identity changed before descriptor read')
        root = Path(proc) / str(pid) / 'fd'
        if type(wait_ms) is not int or not 0 <= wait_ms <= 2000:
            raise ProcessIdentityError('Invalid descriptor wait deadline')
        deadline = monotonic() + wait_ms / 1000
        while True:
            matches = set()
            for entry in listdir(root):
                try:
                    target = readlink(root / entry)
                except FileNotFoundError:
                    continue
                for needle in needles:
                    if needle in target:
                        matches.add(needle)
            after = read_start(pid, proc)
            if after != before or after != expected_ticks:
                raise ProcessIdentityError('Process start identity changed during descriptor read')
            if ready(descriptor):
                raise ProcessIdentityError('Process exited during descriptor read')
            if not matches or monotonic() >= deadline:
                return ProcessFdMatches(pid, after, tuple(needle for needle in needles if needle in matches))
            sleep(.01)
    finally:
        os.close(descriptor)

def parse_witness_request(body, protocol):
    request = protocol.NativeGeometryWitnessRequest()
    try:
        request.ParseFromString(body)
    except Exception as error:
        raise ProcessIdentityError('Malformed root witness request') from error
    _no_unknown_or_noncanonical(request, body)
    if request.request_id != 1 or request.WhichOneof('operation') != 'identity' or not request.identity:
        raise ProcessIdentityError('Unexpected root witness request context')
    return request

def _frame(payload):
    if not 0 < len(payload) <= MAX_FRAME:
        raise ProcessIdentityError('Invalid protobuf frame size')
    return struct.pack('!I', len(payload)) + payload

def _read_exact(stream, count):
    value = bytearray()
    while len(value) < count:
        part = stream.read(count - len(value))
        if not part:
            raise ProcessIdentityError('Truncated protobuf frame')
        value.extend(part)
    return bytes(value)

def privileged_read(expected, *, protocol, runner=subprocess.run,
                    helper=Path(__file__), bindings_dir=None):
    expected = validate_expected(expected)
    if bindings_dir is None:
        bindings_dir = Path(protocol.__file__).resolve().parent
    bindings_dir = Path(bindings_dir).resolve(strict=True)
    if bindings_dir.name != 'python' or not (bindings_dir / 'geometry_native_pb2.py').is_file():
        raise ProcessIdentityError('Invalid generated bindings directory')
    request = protocol.NativeGeometryWitnessRequest(request_id=1, identity=True)
    body = request.SerializeToString(deterministic=True)
    argv = ['sudo', '-n', sys.executable, '-I', str(helper), '--root-witness',
            '--bindings-dir', str(bindings_dir)]
    for pid, ticks in expected:
        argv.extend((str(pid), str(ticks)))
    result = runner(argv, input=_frame(body), capture_output=True, timeout=10)
    if result.returncode or result.stderr:
        raise ProcessIdentityError('Privileged process witness failed')
    output = result.stdout
    if not isinstance(output, bytes) or len(output) < 4:
        raise ProcessIdentityError('Truncated privileged protobuf frame')
    size, = struct.unpack('!I', output[:4])
    if not 0 < size <= MAX_FRAME or len(output) != size + 4:
        raise ProcessIdentityError('Invalid privileged protobuf frame boundary')
    reply = protocol.NativeGeometryWitnessReply()
    try:
        reply.ParseFromString(output[4:])
    except Exception as error:
        raise ProcessIdentityError('Malformed privileged protobuf reply') from error
    _no_unknown_or_noncanonical(reply, output[4:])
    if (reply.request_id != 1 or len(reply.processes) != len(expected) or reply.error or
            reply.HasField('control') or reply.pixels or reply.cleanup_complete):
        raise ProcessIdentityError('Unexpected privileged protobuf reply context')
    result = []
    for (pid, ticks), item in zip(expected, reply.processes):
        if item.pid != pid or item.start_ticks != ticks or len(item.executable_sha256) != 32:
            raise ProcessIdentityError('Stale or malformed privileged process identity')
        result.append(ProcessIdentity(pid, ticks, bytes(item.executable_sha256)))
    return result

def scan_fd_targets(queries, *, wait_until_absent_ms=0, protocol, runner=subprocess.run,
                    helper=Path(__file__), bindings_dir=None):
    queries = validate_fd_queries(queries)
    if type(wait_until_absent_ms) is not int or not 0 <= wait_until_absent_ms <= 2000:
        raise ProcessIdentityError('Invalid descriptor wait deadline')
    if bindings_dir is None:
        bindings_dir = Path(protocol.__file__).resolve().parent
    bindings_dir = Path(bindings_dir).resolve(strict=True)
    if bindings_dir.name != 'python' or not (bindings_dir / 'geometry_native_pb2.py').is_file():
        raise ProcessIdentityError('Invalid generated bindings directory')
    request = protocol.NativeGeometryWitnessRequest(request_id=1)
    scan = request.fd_scan; scan.wait_until_absent_ms = wait_until_absent_ms
    for pid, ticks, needles in queries:
        item = scan.processes.add(pid=pid, start_ticks=ticks)
        item.target_needles.extend(needles)
    body = request.SerializeToString(deterministic=True)
    argv = ['sudo', '-n', sys.executable, '-I', str(helper), '--root-witness-fd',
            '--bindings-dir', str(bindings_dir)]
    result = runner(argv, input=_frame(body), capture_output=True, timeout=10)
    if result.returncode or result.stderr:
        raise ProcessIdentityError('Privileged descriptor witness failed')
    output = result.stdout
    if not isinstance(output, bytes) or len(output) < 4:
        raise ProcessIdentityError('Truncated privileged descriptor frame')
    size, = struct.unpack('!I', output[:4])
    if not 0 < size <= MAX_FRAME or len(output) != size + 4:
        raise ProcessIdentityError('Invalid privileged descriptor frame boundary')
    reply = protocol.NativeGeometryWitnessReply()
    try:
        reply.ParseFromString(output[4:])
    except Exception as error:
        raise ProcessIdentityError('Malformed privileged descriptor reply') from error
    _no_unknown_or_noncanonical(reply, output[4:])
    if (reply.request_id != 1 or len(reply.fd_results) != len(queries) or reply.error or
            reply.HasField('control') or reply.pixels or reply.processes or reply.cleanup_complete):
        raise ProcessIdentityError('Unexpected privileged descriptor reply context')
    result = []
    for (pid, ticks, needles), item in zip(queries, reply.fd_results):
        matched = tuple(item.matched_needles)
        if (item.pid != pid or item.start_ticks != ticks or len(set(matched)) != len(matched) or
                any(value not in needles for value in matched)):
            raise ProcessIdentityError('Stale or malformed privileged descriptor result')
        result.append(ProcessFdMatches(pid, ticks, matched))
    return result

def collect(expected, *, direct=read_identity, privileged=privileged_read,
            protocol=None, bindings_dir=None, on_privileged=None):
    expected = validate_expected(expected)
    try:
        result = [direct(pid, ticks) for pid, ticks in expected]
    except PermissionError:
        if on_privileged is not None:
            on_privileged()
        result = privileged(expected, protocol=protocol, bindings_dir=bindings_dir)
    if len(result) != len(expected):
        raise ProcessIdentityError('Wrong process identity count')
    checked = []
    for (pid, ticks), item in zip(expected, result):
        if (type(item.pid) is not int or type(item.start_ticks) is not int or
                item.pid != pid or item.start_ticks != ticks or
                not isinstance(item.executable_sha256, bytes) or len(item.executable_sha256) != 32):
            raise ProcessIdentityError('Stale or malformed process identity')
        checked.append(ProcessIdentity(pid, ticks, item.executable_sha256))
    return checked

def finish_observer(ready, exchange, recover):
    """Never send protobuf shutdown until a valid ready reply was received."""
    if not ready:
        recover()
        return
    reply = exchange(shutdown=True)
    if not reply.cleanup_complete:
        raise ProcessIdentityError('Observer did not confirm cleanup')

def _parse_cli(argv):
    if len(argv) < 5 or argv[0] != '--root-witness' or argv[1] != '--bindings-dir':
        raise SystemExit('Expected fixed witness mode and generated bindings directory')
    bindings = Path(argv[2]).resolve(strict=True)
    if bindings.name != 'python' or not (bindings / 'geometry_native_pb2.py').is_file():
        raise SystemExit('Invalid generated bindings directory')
    values = argv[3:]
    if not values or len(values) % 2:
        raise SystemExit('Expected numeric PID/start-tick pairs')
    expected = []
    for pid, ticks in zip(values[::2], values[1::2]):
        if re.fullmatch(r'[1-9][0-9]*', pid) is None or re.fullmatch(r'[1-9][0-9]*', ticks) is None:
            raise SystemExit('Invalid numeric process identity')
        expected.append((int(pid), int(ticks)))
    return bindings, validate_expected(expected)

def _parse_fd_cli(argv):
    if (len(argv) != 3 or argv[0] != '--root-witness-fd' or
            argv[1] != '--bindings-dir'):
        raise SystemExit('Expected fixed descriptor witness mode and generated bindings directory')
    bindings = Path(argv[2]).resolve(strict=True)
    if bindings.name != 'python' or not (bindings / 'geometry_native_pb2.py').is_file():
        raise SystemExit('Invalid generated bindings directory')
    return bindings

def _read_root_request(stdin):
    header = _read_exact(stdin.buffer, 4)
    size, = struct.unpack('!I', header)
    if not 0 < size <= MAX_FRAME:
        raise ProcessIdentityError('Invalid request protobuf frame size')
    body = _read_exact(stdin.buffer, size)
    if stdin.buffer.read(1):
        raise ProcessIdentityError('Unexpected trailing request bytes')
    return body

def root_witness(argv, stdin, stdout):
    try:
        bindings, expected = _parse_cli(argv)
        if os.geteuid() != 0:
            raise SystemExit('Root witness requires approved sudo')
        if not sys.flags.isolated:
            raise SystemExit('Root witness requires isolated Python')
        # Keep Python -I system paths, including distro-provided protobuf.
        # Isolated mode excludes PYTHONPATH, user site, and the script directory.
        sys.path.insert(0, str(bindings))
        import geometry_native_pb2 as protocol
        header = _read_exact(stdin.buffer, 4)
        size, = struct.unpack('!I', header)
        if not 0 < size <= MAX_FRAME:
            raise ProcessIdentityError('Invalid request protobuf frame size')
        body = _read_exact(stdin.buffer, size)
        if stdin.buffer.read(1):
            raise ProcessIdentityError('Unexpected trailing request bytes')
        request = parse_witness_request(body, protocol)
        reply = protocol.NativeGeometryWitnessReply(request_id=request.request_id)
        for pid, ticks in expected:
            item = read_identity(pid, ticks)
            reply.processes.add(pid=item.pid, start_ticks=item.start_ticks,
                                executable_sha256=item.executable_sha256)
        stdout.buffer.write(_frame(reply.SerializeToString(deterministic=True)))
        stdout.buffer.flush()
        return 0
    except SystemExit:
        raise
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        return 1

def root_fd_witness(argv, stdin, stdout):
    try:
        bindings = _parse_fd_cli(argv)
        if os.geteuid() != 0:
            raise SystemExit('Root witness requires approved sudo')
        if not sys.flags.isolated:
            raise SystemExit('Root witness requires isolated Python')
        sys.path.insert(0, str(bindings))
        import geometry_native_pb2 as protocol
        request, queries, wait_ms = parse_fd_scan_request(_read_root_request(stdin), protocol)
        deadline = time.monotonic() + wait_ms / 1000
        while True:
            results = [read_fd_matches(pid, ticks, needles)
                       for pid, ticks, needles in queries]
            if not any(result.matched_needles for result in results) or time.monotonic() >= deadline:
                break
            time.sleep(.01)
        reply = protocol.NativeGeometryWitnessReply(request_id=request.request_id)
        for result in results:
            reply.fd_results.add(pid=result.pid, start_ticks=result.start_ticks,
                                 matched_needles=result.matched_needles)
        stdout.buffer.write(_frame(reply.SerializeToString(deterministic=True)))
        stdout.buffer.flush()
        return 0
    except SystemExit:
        raise
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        return 1

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == '--root-witness-fd':
        raise SystemExit(root_fd_witness(sys.argv[1:], sys.stdin, sys.stdout))
    raise SystemExit(root_witness(sys.argv[1:], sys.stdin, sys.stdout))
