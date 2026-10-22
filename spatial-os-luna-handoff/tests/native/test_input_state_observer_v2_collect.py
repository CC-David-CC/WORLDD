"""Focused Python transport controls. Native runtime remains unrun.

Supply generated protobuf bindings and this directory through PYTHONPATH.
"""
import errno
import fcntl
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

import input_state_observer_v2_collect as collector
import input_state_observer_v2 as v2
import test_input_state_observer_v2 as builder


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.sample = builder.fixture()
        self._set_live_arm(self.sample)
        self.payload = builder.artifact(self.sample.records)
        self.kwargs = dict(expected_session_id=builder.SID,
            expected_arm_id=builder.AID,
            expected_native_session_epoch=builder.NATIVE,
            expected_arm=self.sample.arm, expected_bindings=self.sample.bindings)

    @staticmethod
    def _set_live_arm(sample):
        now_ns = time.monotonic_ns()
        delta = now_ns - 100
        arm = sample.arm
        arm.arm_monotonic_ns = now_ns
        arm.deadline_monotonic_ns = now_ns + 2_000_000_000
        sample.records[0].arm.accepted_command.CopyFrom(arm)
        for snapshot in sample.records[1].native_transition.snapshots:
            snapshot.monotonic_ns += delta
        sample.records[2].observer_press.callback_monotonic_ns += delta
        for receipt in sample.records[3].probe_receipts.receipts:
            receipt.monotonic_ns += delta

    @staticmethod
    def _identity(fd):
        info = os.fstat(fd)
        return info.st_dev, info.st_ino, info.st_mode

    def _close_if_same(self, fd, identity):
        try:
            if self._identity(fd) == identity:
                os.close(fd)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise

    def pipe(self, parts, *, pipe_size=None, delay=0, keep_open=False):
        """Write concurrently without relying on the kernel's pipe capacity."""
        rfd, wfd = os.pipe()
        identity = self._identity(rfd)
        real_close = os.close
        stop = threading.Event()
        state = dict(written=0, broken_pipe=False, errors=[], stop=stop)
        try:
            if pipe_size is not None:
                fcntl.fcntl(wfd, fcntl.F_SETPIPE_SZ, pipe_size)
                state["capacity"] = fcntl.fcntl(wfd, fcntl.F_GETPIPE_SZ)
            os.set_blocking(wfd, False)
        except BaseException:
            real_close(rfd)
            real_close(wfd)
            raise

        def sender():
            try:
                for part in parts:
                    pending = memoryview(part)
                    while pending and not stop.is_set():
                        try:
                            count = os.write(wfd, pending)
                        except BlockingIOError:
                            stop.wait(0.002)
                            continue
                        pending = pending[count:]
                        state["written"] += count
                    if stop.wait(delay):
                        break
                if keep_open:
                    stop.wait()
            except BrokenPipeError:
                state["broken_pipe"] = True
            except BaseException as exc:
                state["errors"].append(exc)
            finally:
                real_close(wfd)

        thread = threading.Thread(target=sender)
        state["thread"] = thread

        def cleanup():
            # Close any unconsumed reader before joining. The writer never blocks.
            self._close_if_same(rfd, identity)
            stop.set()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive(), "pipe writer did not stop")
            self.assertEqual(state["errors"], [])

        self.addCleanup(cleanup)
        thread.start()
        return rfd, state

    def ready_pipe(self, payload=None, **kwargs):
        return self.pipe([self.payload if payload is None else payload], **kwargs)[0]

    def ready_socket(self):
        reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            writer.sendall(self.payload)
        finally:
            writer.close()
        fd = reader.detach()
        self.addCleanup(self._close_if_same, fd, self._identity(fd))
        return fd

    def collect(self, fd, **overrides):
        return collector.collect(fd, **(self.kwargs | overrides))

    def assert_closed(self, fd):
        with self.assertRaises(OSError) as caught:
            os.fstat(fd)
        self.assertEqual(caught.exception.errno, errno.EBADF)

    def assert_rejected(self, fd, code, error=collector.ObservationCollectionError,
                        **kwargs):
        with self.assertRaises(error) as caught:
            self.collect(fd, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assert_closed(fd)

    def test_fragmented_pipe_and_unix_stream_success(self):
        fd, state = self.pipe([self.payload[:1], self.payload[1:19],
                               self.payload[19:]], delay=0.01)
        self.assertEqual(self.collect(fd).observer_ordinal, 1)
        state["thread"].join(timeout=1)
        self.assertFalse(state["thread"].is_alive())
        self.assert_closed(fd)

        reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(writer.close)
        fd = reader.detach()
        self.addCleanup(self._close_if_same, fd, self._identity(fd))
        errors = []
        def sender():
            try:
                for part in (self.payload[:1], self.payload[1:19], self.payload[19:]):
                    writer.sendall(part)
                    time.sleep(0.01)
            except OSError as exc:
                errors.append(exc)
            finally:
                writer.close()
        writer.settimeout(0.5)
        thread = threading.Thread(target=sender)
        thread.start()
        try:
            self.assertEqual(self.collect(fd).observer_ordinal, 1)
        finally:
            thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assert_closed(fd)

    def test_eof_trailing_bytes_and_partial_frame_exact_errors(self):
        for payload, code in ((self.payload + b"\x00", "RECORD_COUNT_LIMIT"),
                              (self.payload[:-1], "PROTOBUF_TRUNCATED"),
                              (b"", "FOUR_RECORDS_REQUIRED")):
            with self.subTest(code=code):
                self.assert_rejected(self.ready_pipe(payload), code,
                                     v2.ObservationV2Error)

    def test_oversized_artifact_through_narrow_pipe_closes_reader_and_joins_writer(self):
        payload = b"x" * (v2.MAX_ARTIFACT_BYTES * 4)
        fd, state = self.pipe([payload], pipe_size=4096)
        self.assertLessEqual(state["capacity"], 4096)
        self.assert_rejected(fd, "ARTIFACT_BYTES_LIMIT")
        state["thread"].join(timeout=1)
        self.assertFalse(state["thread"].is_alive())
        self.assertGreaterEqual(state["written"], v2.MAX_ARTIFACT_BYTES + 1)
        self.assertLess(state["written"], len(payload))
        self.assertTrue(state["broken_pipe"])

    def test_frame_record_and_receipt_limits(self):
        for payload, code in ((b"\x81\x20" + b"x" * 10, "FRAME_BYTES_LIMIT"),
                (self.payload + builder.artifact([self.sample.records[0]]),
                 "RECORD_COUNT_LIMIT")):
            self.assert_rejected(self.ready_pipe(payload), code,
                                 v2.ObservationV2Error)
        for _ in range(29):
            self.sample.records[3].probe_receipts.receipts.add()
        payload = builder.artifact(self.sample.records)
        self.assert_rejected(self.ready_pipe(payload), "RECEIPT_COUNT_INVALID",
                             v2.ObservationV2Error)

    def test_trusted_arm_and_bindings_snapshotted_before_wait(self):
        fd = self.ready_pipe()
        arm = type(self.sample.arm)()
        arm.CopyFrom(self.sample.arm)
        bindings = []
        for original in self.sample.bindings:
            binding = type(original)()
            binding.CopyFrom(original)
            bindings.append(binding)
        kwargs = self.kwargs | {"expected_arm": arm, "expected_bindings": bindings}
        original_select = collector.selectors.DefaultSelector.select
        def mutate_after_snapshot(selector, timeout=None):
            arm.arm_id = b"mutated"
            bindings[0].actor.actor_id = b"mutated"
            return original_select(selector, timeout)
        with mock.patch.object(collector.selectors.DefaultSelector, "select",
                               new=mutate_after_snapshot):
            result = collector.collect(fd, **kwargs)
        self.assertEqual(result.observer_ordinal, 1)
        self.assertEqual(arm.arm_id, b"mutated")
        self.assertEqual(bindings[0].actor.actor_id, b"mutated")
        self.assert_closed(fd)

    def test_blocking_mode_restored_before_owned_fd_close(self):
        for make_stream in (self.ready_pipe, self.ready_socket):
            for initially_blocking in (True, False):
                fd = make_stream()
                os.set_blocking(fd, initially_blocking)
                real_close = os.close
                observed = []
                def checked_close(target):
                    if target == fd:
                        observed.append(os.get_blocking(target))
                    real_close(target)
                with mock.patch.object(collector.os, "close", side_effect=checked_close):
                    self.assertEqual(self.collect(fd).observer_ordinal, 1)
                self.assertEqual(observed, [initially_blocking])
                self.assert_closed(fd)

    def test_missing_trusted_context_closes_owned_fd(self):
        fd = self.ready_pipe()
        with self.assertRaises(collector.ObservationCollectionError) as caught:
            collector.collect(fd)
        self.assertEqual(caught.exception.code, "EXPECTED_SESSION_INVALID")
        self.assert_closed(fd)

    def test_invalid_timeout_and_arm_intervals_close_owned_fd(self):
        for timeout in (True, 0, -1, 10.01, float("nan"), float("inf"), "1"):
            with self.subTest(timeout=timeout):
                self.assert_rejected(self.ready_pipe(), "TIMEOUT_INVALID",
                                     timeout_seconds=timeout)
        for interval in (0, -1, v2.MAX_ARM_INTERVAL_NS + 1):
            arm = type(self.sample.arm)()
            arm.CopyFrom(self.sample.arm)
            arm.deadline_monotonic_ns = arm.arm_monotonic_ns + interval
            self.assert_rejected(self.ready_pipe(), "ARM_INTERVAL_INVALID",
                                 expected_arm=arm)

    def test_invalid_fd_values_never_transfer_ownership_or_close_stdio(self):
        for fd in (True, False, -1, -100, 0x80000000, 1 << 100, 1.0, "3", None):
            with self.subTest(fd=fd):
                with mock.patch.object(collector.os, "close") as close:
                    with self.assertRaises(collector.ObservationCollectionError) as caught:
                        self.collect(fd)
                self.assertEqual(caught.exception.code, "FD_INVALID")
                close.assert_not_called()
        for fd in (0, 1, 2):
            os.fstat(fd)

    def test_closed_native_fd_returns_typed_invalid(self):
        fd, writer = os.pipe()
        os.close(fd)
        os.close(writer)
        self.assert_rejected(fd, "FD_INVALID")

    def test_write_only_fifo_rejected_before_any_wait(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        with mock.patch.object(collector.selectors, "DefaultSelector") as selector:
            self.assert_rejected(write_fd, "FD_ACCESS_INVALID")
        selector.assert_not_called()

    def test_wait_read_eintr_and_eagain_retry_with_one_deadline(self):
        fd = self.ready_pipe()
        real_select = collector.selectors.DefaultSelector.select
        real_read = os.read
        injected = set()
        wait_timeouts = []
        def retry_select(selector, timeout=None):
            wait_timeouts.append(timeout)
            if "wait_eintr" not in injected:
                injected.add("wait_eintr")
                raise OSError(errno.EINTR, "interrupted wait")
            return real_select(selector, timeout)
        def retry_read(read_fd, count):
            if "read_eintr" not in injected:
                injected.add("read_eintr")
                raise OSError(errno.EINTR, "interrupted read")
            if "read_eagain" not in injected:
                injected.add("read_eagain")
                raise BlockingIOError(errno.EAGAIN, "try again")
            return real_read(read_fd, count)
        with mock.patch.object(collector.selectors.DefaultSelector, "select",
                               new=retry_select), mock.patch.object(
                                   collector.os, "read", side_effect=retry_read):
            result = self.collect(fd, timeout_seconds=0.5)
        self.assertEqual(result.observer_ordinal, 1)
        self.assertEqual(injected, {"wait_eintr", "read_eintr", "read_eagain"})
        self.assertTrue(all(0 < value <= 0.5 for value in wait_timeouts))
        self.assertTrue(all(later <= earlier for earlier, later
                            in zip(wait_timeouts, wait_timeouts[1:])))
        self.assertLess(wait_timeouts[-1], wait_timeouts[0])
        self.assert_closed(fd)

    def test_late_data_rejected_after_read(self):
        fd = self.ready_pipe()
        now = time.monotonic_ns()
        clock = [now]
        real_read = os.read
        def late_read(target, count):
            chunk = real_read(target, count)
            clock[0] = now + 1_000_000_000
            return chunk
        with mock.patch.object(collector.time, "monotonic_ns", side_effect=lambda: clock[0]), \
             mock.patch.object(collector.os, "read", side_effect=late_read):
            self.assert_rejected(fd, "DEADLINE_EXPIRED", timeout_seconds=0.5)

    def test_trusted_arm_deadline_caps_longer_timeout(self):
        rfd, wfd = os.pipe()
        self.addCleanup(os.close, wfd)
        arm = type(self.sample.arm)()
        arm.CopyFrom(self.sample.arm)
        now_ns = time.monotonic_ns()
        arm.arm_monotonic_ns = now_ns - 1_000_000
        arm.deadline_monotonic_ns = now_ns + 50_000_000
        started = time.monotonic()
        self.assert_rejected(rfd, "DEADLINE_EXPIRED", expected_arm=arm,
                             timeout_seconds=10.0)
        self.assertLess(time.monotonic() - started, 0.25)

    def test_decoder_failure_after_deadline_reports_expiry(self):
        fd = self.ready_pipe()
        now = time.monotonic_ns()
        clock = [now]
        def late_failure(*args, **kwargs):
            clock[0] = now + 1_000_000_000
            raise v2.ObservationV2Error("TEST_SEMANTIC_FAILURE")
        with mock.patch.object(collector.time, "monotonic_ns", side_effect=lambda: clock[0]), \
             mock.patch.object(collector.observer_v2, "decode_and_validate", side_effect=late_failure):
            self.assert_rejected(fd, "DEADLINE_EXPIRED", timeout_seconds=0.5)

    def test_successful_decode_after_deadline_cannot_return_success(self):
        fd = self.ready_pipe()
        now = time.monotonic_ns()
        clock = [now]
        real_decode = collector.observer_v2.decode_and_validate
        def late_decode(*args, **kwargs):
            result = real_decode(*args, **kwargs)
            clock[0] = now + 1_000_000_000
            return result
        with mock.patch.object(collector.time, "monotonic_ns", side_effect=lambda: clock[0]), \
             mock.patch.object(collector.observer_v2, "decode_and_validate", side_effect=late_decode):
            self.assert_rejected(fd, "DEADLINE_EXPIRED", timeout_seconds=0.5)

    def test_trickle_cannot_extend_absolute_deadline(self):
        fd, state = self.pipe([b"\x00"] * 20, delay=0.02)
        started = time.monotonic()
        self.assert_rejected(fd, "DEADLINE_EXPIRED", timeout_seconds=0.07)
        elapsed = time.monotonic() - started
        state["thread"].join(timeout=1)
        self.assertFalse(state["thread"].is_alive())
        self.assertLess(elapsed, 0.25)

    def test_valid_frames_without_eof_cannot_finish_early(self):
        fd, _ = self.pipe([self.payload], keep_open=True)
        self.assert_rejected(fd, "DEADLINE_EXPIRED", timeout_seconds=0.03)

    def test_reject_regular_datagram_internet_and_unconnected_unix(self):
        with tempfile.TemporaryFile() as file:
            self.assert_rejected(os.dup(file.fileno()), "FD_CLASS_INVALID")
        datagram, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        peer.close()
        cases = [(datagram, "SOCKET_TYPE_INVALID"),
                 (socket.socket(socket.AF_INET, socket.SOCK_STREAM), "SOCKET_FAMILY_INVALID"),
                 (socket.socket(socket.AF_UNIX, socket.SOCK_STREAM), "SOCKET_INVALID")]
        for sock, _ in cases:
            self.addCleanup(sock.close)
        for sock, code in cases:
            with self.subTest(code=code):
                self.assert_rejected(sock.detach(), code)

    def test_semantic_rejection_uses_consistent_trusted_fixture(self):
        # The accepted arm and trusted arm still match. Only the event is wrong.
        self.sample.records[2].observer_press.detail = 2
        self.assert_rejected(self.ready_pipe(builder.artifact(self.sample.records)),
                             "OBSERVER_EVENT_JOIN", v2.ObservationV2Error)

    def test_restore_failure_surfaces_typed_error_and_still_closes(self):
        fd = self.ready_pipe()
        real_set = os.set_blocking
        def fail_restore(target, blocking):
            if target == fd and blocking:
                raise OSError(errno.EIO, "injected restore failure")
            return real_set(target, blocking)
        with mock.patch.object(collector.os, "set_blocking", side_effect=fail_restore):
            self.assert_rejected(fd, "FD_RESTORE_FAILED")

    def test_close_failure_surfaces_typed_error_without_retry(self):
        for close_errno in (errno.EIO, errno.EINTR):
            fd = self.ready_pipe()
            real_close = os.close
            calls = []
            def fail_close(target):
                if target == fd:
                    calls.append(target)
                    # Model Linux: the fd is released before close reports error.
                    real_close(target)
                    raise OSError(close_errno, "injected close failure")
                return real_close(target)
            with mock.patch.object(collector.os, "close", side_effect=fail_close):
                self.assert_rejected(fd, "FD_CLOSE_FAILED")
            self.assertEqual(calls, [fd])

    def test_cleanup_failure_preserves_original_semantic_or_transport_error(self):
        for semantic in (False, True):
            payload = self.payload
            if semantic:
                self.sample.records[2].observer_press.detail = 2
                payload = builder.artifact(self.sample.records)
            fd = self.ready_pipe(payload)
            real_set, real_close = os.set_blocking, os.close
            calls = []
            def fail_restore(target, blocking):
                if target == fd and blocking:
                    raise OSError(errno.EIO, "injected restore failure")
                return real_set(target, blocking)
            def fail_close(target):
                if target == fd:
                    calls.append(target)
                    raise OSError(errno.EIO, "injected close failure before release")
                return real_close(target)
            try:
                with mock.patch.object(collector.os, "set_blocking", side_effect=fail_restore), \
                     mock.patch.object(collector.os, "close", side_effect=fail_close):
                    if semantic:
                        self.assert_rejected_without_close(fd, "OBSERVER_EVENT_JOIN", v2.ObservationV2Error)
                    else:
                        with mock.patch.object(collector.os, "read", side_effect=OSError(errno.EIO, "read failed")):
                            self.assert_rejected_without_close(fd, "READ_FAILED", collector.ObservationCollectionError)
                self.assertEqual(calls, [fd])
            finally:
                # The injected close did not release it. The test owns cleanup.
                real_close(fd)
            self.assert_closed(fd)

    def assert_rejected_without_close(self, fd, code, error):
        with self.assertRaises(error) as caught:
            self.collect(fd)
        self.assertEqual(caught.exception.code, code)

    def test_cleanup_failure_is_reported_inside_callers_exception_handler(self):
        fd = self.ready_pipe()
        real_set = os.set_blocking
        def fail_restore(target, blocking):
            if target == fd and blocking:
                raise OSError(errno.EIO, "injected restore failure")
            return real_set(target, blocking)
        try:
            raise ValueError("unrelated caller exception")
        except ValueError:
            with mock.patch.object(collector.os, "set_blocking", side_effect=fail_restore):
                self.assert_rejected(fd, "FD_RESTORE_FAILED")

    def test_cleanup_preserves_the_original_failure_instance(self):
        fd = self.ready_pipe()
        failure = v2.ObservationV2Error("TEST_ORIGINAL_FAILURE")
        real_set = os.set_blocking
        def fail_restore(target, blocking):
            if target == fd and blocking:
                raise OSError(errno.EIO, "injected restore failure")
            return real_set(target, blocking)
        with mock.patch.object(collector.os, "set_blocking", side_effect=fail_restore), \
             mock.patch.object(collector.observer_v2, "decode_and_validate", side_effect=failure):
            with self.assertRaises(v2.ObservationV2Error) as caught:
                self.collect(fd)
        self.assertIs(caught.exception, failure)
        self.assert_closed(fd)

    def test_selector_close_failure_still_restores_and_closes_owned_fd(self):
        fd = self.ready_pipe()
        real_selector_close = collector.selectors.DefaultSelector.close
        def fail_selector_close(selector):
            real_selector_close(selector)
            raise OSError(errno.EIO, "injected selector cleanup failure")
        with mock.patch.object(collector.selectors.DefaultSelector, "close", new=fail_selector_close):
            self.assert_rejected(fd, "SELECTOR_CLOSE_FAILED")


if __name__ == "__main__":
    unittest.main()
