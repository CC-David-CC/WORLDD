"""Bounded collector for the finite M09 native input observation artifact."""
import math
import os
import select
import stat
import time
import fcntl
import socket
import sys
from input_state_observer import Limits, ObservationError, decode_records, _check_limits
DEFAULT_TOTAL_DEADLINE_SECONDS = 2.0
MAX_TOTAL_DEADLINE_SECONDS = 10.0
READ_CHUNK_BYTES = 4096
def collect_artifact(fd, *, timeout_seconds=DEFAULT_TOTAL_DEADLINE_SECONDS, limits=Limits()):
    """Consume one owned nonblocking pipe/socket descriptor through EOF.

    Ownership transfers on entry when fd is an exact int. The descriptor is
    closed on success and every failure path. It must be a nonblocking FIFO or
    stream socket, with no other reader or owner. The timeout is one monotonic
    total deadline, including the wait for EOF. Returned bytes have exactly
    three canonical protobuf frames. Identity and provenance validation stays
    with the existing validator.
    """
    # Linux system-call descriptor arguments use a signed native int. Reject
    # larger Python integers before ownership transfer or cleanup can overflow.
    if type(fd) is not int or fd < 0 or fd > 0x7fffffff:
        raise ObservationError("invalid owned descriptor")
    try:
        if type(timeout_seconds) not in (int, float):
            raise ObservationError("invalid total deadline")
        try:
            timeout = float(timeout_seconds)
        except (OverflowError, ValueError) as exc:
            raise ObservationError("invalid total deadline") from exc
        if (not math.isfinite(timeout) or timeout <= 0 or
                timeout > MAX_TOTAL_DEADLINE_SECONDS):
            raise ObservationError("invalid total deadline")
        if type(limits) is not Limits:
            raise ObservationError("invalid limits type")
        _check_limits(limits)
        cap = limits.max_artifact_bytes
        try:
            mode = os.fstat(fd).st_mode
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        except OSError as exc:
            raise ObservationError("descriptor inspection failed") from exc
        if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
            raise ObservationError("descriptor must be a pipe or stream socket")
        if stat.S_ISSOCK(mode):
            try:
                socket_probe = socket.socket(fileno=os.dup(fd))
                try:
                    if socket_probe.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
                        raise ObservationError("socket descriptor must be stream type")
                finally:
                    socket_probe.close()
            except OSError as exc:
                raise ObservationError("socket descriptor inspection failed") from exc
        if not flags & os.O_NONBLOCK:
            raise ObservationError("descriptor must already be nonblocking")
        deadline = time.monotonic() + timeout
        chunks = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ObservationError("total artifact deadline expired")
            try:
                readable, _, exceptional = select.select([fd], [], [fd], remaining)
            except (OSError, ValueError) as exc:
                raise ObservationError("descriptor wait failed") from exc
            if time.monotonic() >= deadline:
                raise ObservationError("total artifact deadline expired")
            if exceptional:
                raise ObservationError("descriptor reported an exceptional condition")
            if not readable:
                raise ObservationError("total artifact deadline expired")
            amount = min(READ_CHUNK_BYTES, cap + 1 - len(chunks))
            try:
                piece = os.read(fd, amount)
            except BlockingIOError:
                continue
            except OSError as exc:
                raise ObservationError("descriptor read failed") from exc
            if time.monotonic() >= deadline:
                raise ObservationError("total artifact deadline expired")
            if not piece:
                artifact = bytes(chunks)
                decode_records(artifact, limits)
                return artifact
            chunks.extend(piece)
            if len(chunks) > cap:
                raise ObservationError("artifact byte limit")
    finally:
        pending_error = sys.exc_info()[0]
        try:
            os.close(fd)
        except OSError as exc:
            if pending_error is None:
                raise ObservationError("descriptor close failed") from exc
