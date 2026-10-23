"""Bounded one-shot collector for the additive native lifecycle stream.

V2 transport ownership and absolute-deadline semantics remain unchanged.
"""
import errno
import fcntl
import os
import selectors
import socket
import stat
import time

import input_state_observer_v3 as observer_v3

MAX_COLLECTION_SECONDS = 10.0
MAX_ARTIFACT_BYTES = observer_v3.MAX_ARTIFACT_BYTES


class ObservationCollectionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise ObservationCollectionError(code)


def _validate_fd_shape(fd):
    try:
        mode = os.fstat(fd).st_mode
    except OSError as exc:
        raise ObservationCollectionError("FD_INVALID") from exc
    if stat.S_ISFIFO(mode):
        try:
            access = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
        except OSError as exc:
            raise ObservationCollectionError("FD_INVALID") from exc
        _require(access != os.O_WRONLY, "FD_ACCESS_INVALID")
        return
    _require(stat.S_ISSOCK(mode), "FD_CLASS_INVALID")
    wrapped = None
    try:
        wrapped = socket.socket(fileno=fd)
        sock_type = wrapped.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
        family = wrapped.getsockopt(socket.SOL_SOCKET, socket.SO_DOMAIN)
        if family == socket.AF_UNIX:
            wrapped.getpeername()
    except OSError as exc:
        raise ObservationCollectionError("SOCKET_INVALID") from exc
    finally:
        if wrapped is not None:
            wrapped.detach()
    _require(sock_type == socket.SOCK_STREAM, "SOCKET_TYPE_INVALID")
    _require(family == socket.AF_UNIX, "SOCKET_FAMILY_INVALID")


def _check_deadline(deadline_ns):
    if time.monotonic_ns() >= deadline_ns:
        raise ObservationCollectionError("DEADLINE_EXPIRED")


def collect(fd, *, expected_session_id=None, expected_arm_id=None,
            expected_native_session_epoch=None, expected_arm=None,
            expected_bindings=None, timeout_seconds=MAX_COLLECTION_SECONDS):
    """Own a native integer fd, collect through EOF, then validate v3.

    Invalid FD values transfer no ownership. Cleanup always attempts close once.
    A transport or semantic failure takes precedence over cleanup failures.
    """
    _require(type(fd) is int and 0 <= fd <= 0x7fffffff, "FD_INVALID")
    deadline_ns = None
    selector = None
    old_blocking = None
    changed_blocking = False
    failed = False
    try:
        entry_ns = time.monotonic_ns()
        _require(type(expected_session_id) is bytes
                 and len(expected_session_id) == 16
                 and type(expected_arm_id) is bytes and len(expected_arm_id) == 16
                 and type(expected_native_session_epoch) is int
                 and expected_native_session_epoch > 0,
                 "EXPECTED_SESSION_INVALID")
        _require(type(expected_arm) is observer_v3.pb.LifecycleArmV3,
                 "EXPECTED_ARM_TYPE")
        _require(type(expected_bindings) in (list, tuple)
                 and len(expected_bindings) == 2
                 and all(type(binding) is observer_v3.v2_pb.ObserverActorSeatBindingV2
                         for binding in expected_bindings),
                 "BINDING_TYPE")
        arm_snapshot = observer_v3.pb.LifecycleArmV3()
        arm_snapshot.CopyFrom(expected_arm)
        binding_snapshots = []
        for binding in expected_bindings:
            copy = observer_v3.v2_pb.ObserverActorSeatBindingV2()
            copy.CopyFrom(binding)
            binding_snapshots.append(copy)
        _require(type(timeout_seconds) in (int, float)
                 and type(timeout_seconds) is not bool
                 and 0 < timeout_seconds <= MAX_COLLECTION_SECONDS,
                 "TIMEOUT_INVALID")
        arm_start_ns = arm_snapshot.common.arm_monotonic_ns
        arm_deadline_ns = arm_snapshot.common.deadline_monotonic_ns
        _require(type(arm_start_ns) is int and type(arm_deadline_ns) is int
                 and arm_start_ns > 0
                 and 0 < arm_deadline_ns - arm_start_ns
                 <= observer_v3.v2.MAX_ARM_INTERVAL_NS,
                 "ARM_INTERVAL_INVALID")
        deadline_ns = min(entry_ns + int(timeout_seconds * 1_000_000_000),
                          arm_deadline_ns)
        _check_deadline(deadline_ns)
        _validate_fd_shape(fd)
        old_blocking = os.get_blocking(fd)
        if old_blocking:
            changed_blocking = True
            os.set_blocking(fd, False)
        selector = selectors.DefaultSelector()
        selector.register(fd, selectors.EVENT_READ)
        data = bytearray()
        while True:
            _check_deadline(deadline_ns)
            remaining_ns = deadline_ns - time.monotonic_ns()
            _check_deadline(deadline_ns)
            try:
                ready = selector.select(remaining_ns / 1_000_000_000)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    _check_deadline(deadline_ns)
                    continue
                raise ObservationCollectionError("WAIT_FAILED") from exc
            _check_deadline(deadline_ns)
            if not ready:
                raise ObservationCollectionError("DEADLINE_EXPIRED")
            _check_deadline(deadline_ns)
            try:
                chunk = os.read(fd, min(4096, MAX_ARTIFACT_BYTES + 1 - len(data)))
            except OSError as exc:
                if exc.errno in (errno.EINTR, errno.EAGAIN, errno.EWOULDBLOCK):
                    _check_deadline(deadline_ns)
                    continue
                raise ObservationCollectionError("READ_FAILED") from exc
            _check_deadline(deadline_ns)
            if not chunk:
                _check_deadline(deadline_ns)
                break
            data.extend(chunk)
            _require(len(data) <= MAX_ARTIFACT_BYTES, "ARTIFACT_BYTES_LIMIT")
        _check_deadline(deadline_ns)
        try:
            result = observer_v3.decode_and_validate(
                bytes(data), expected_session_id=expected_session_id,
                expected_arm_id=expected_arm_id,
                expected_native_session_epoch=expected_native_session_epoch,
                expected_arm=arm_snapshot, expected_bindings=binding_snapshots)
        except Exception:
            _check_deadline(deadline_ns)
            raise
        _check_deadline(deadline_ns)
        return result
    except BaseException:
        failed = True
        raise
    finally:
        cleanup_errors = []
        if selector is not None:
            try:
                selector.close()
            except OSError as exc:
                cleanup_errors.append(("SELECTOR_CLOSE_FAILED", exc))
        if changed_blocking:
            try:
                os.set_blocking(fd, old_blocking)
            except OSError as exc:
                cleanup_errors.append(("FD_RESTORE_FAILED", exc))
        try:
            os.close(fd)
        except OSError as exc:
            # Linux releases the fd even when close reports EINTR. Never retry.
            cleanup_errors.append(("FD_CLOSE_FAILED", exc))
        if cleanup_errors and not failed:
            code, cause = cleanup_errors[0]
            raise ObservationCollectionError(code) from cause
