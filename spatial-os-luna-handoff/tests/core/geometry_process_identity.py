"""Narrow protobuf process-identity witness for native geometry fixtures."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import sysconfig
import sys

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

def root_witness(argv, stdin, stdout):
    try:
        bindings, expected = _parse_cli(argv)
        if os.geteuid() != 0:
            raise SystemExit('Root witness requires approved sudo')
        paths = sysconfig.get_paths()
        safe_paths = [paths[name] for name in ('stdlib','platstdlib','purelib','platlib')
                      if Path(paths[name]).is_dir()]
        sys.path[:] = [str(bindings), *dict.fromkeys(safe_paths)]
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

if __name__ == '__main__':
    raise SystemExit(root_witness(sys.argv[1:], sys.stdin, sys.stdout))
