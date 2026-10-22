"""Source regressions for the bounded M09 descriptor collector.

These cases are intentionally not executed on the development host assignment.
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

from input_state_observer import Limits, ObservationError
from input_state_observer_collector import collect_artifact
class BoundedCollectorTests(unittest.TestCase):
    def setUp(self):
        # Import the parser fixture locally so unittest does not rediscover its
        # TestCase class as part of this collector-only module.
        from test_input_state_observer import ObserverArtifactTests, framed
        fixture = ObserverArtifactTests()
        fixture.setUp()
        self.artifact = framed(fixture.records)

    def pipe(self):
        read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        return read_fd, write_fd

    def assert_closed(self, fd):
        with self.assertRaises(OSError):
            fcntl.fcntl(fd, fcntl.F_GETFD)

    def test_exact_three_record_artifact_succeeds_only_after_pipe_eof(self):
        read_fd, write_fd = self.pipe()
        os.write(write_fd, self.artifact)
        os.close(write_fd)
        self.assertEqual(collect_artifact(read_fd), self.artifact)
        self.assert_closed(read_fd)

    def test_stream_socket_exact_artifact_succeeds_after_peer_close(self):
        receiver, sender = socket.socketpair()
        receiver.setblocking(False)
        sender.sendall(self.artifact)
        sender.close()
        receiver_fd = receiver.detach()
        self.assertEqual(collect_artifact(receiver_fd), self.artifact)
        self.assert_closed(receiver_fd)

    def test_open_writer_after_third_record_fails_total_deadline_and_closes_reader(self):
        read_fd, write_fd = self.pipe()
        os.write(write_fd, self.artifact)
        started = time.monotonic()
        with self.assertRaisesRegex(ObservationError, "deadline"):
            collect_artifact(read_fd, timeout_seconds=0.08)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assert_closed(read_fd)
        os.close(write_fd)

    def test_slow_trickle_uses_one_total_deadline(self):
        read_fd, write_fd = self.pipe()
        def trickle():
            try:
                for byte in self.artifact:
                    os.write(write_fd, bytes((byte,)))
                    time.sleep(0.03)
            except OSError:
                pass
            finally:
                os.close(write_fd)
        writer = threading.Thread(target=trickle, daemon=True)
        writer.start()
        started = time.monotonic()
        with self.assertRaisesRegex(ObservationError, "deadline"):
            collect_artifact(read_fd, timeout_seconds=0.08)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.5)
        self.assert_closed(read_fd)
        writer.join(0.5)
        self.assertFalse(writer.is_alive())

    def test_trailing_bytes_and_extra_frame_are_rejected(self):
        for suffix in (b"x", b"\x01\x00"):
            read_fd, write_fd = self.pipe()
            os.write(write_fd, self.artifact + suffix)
            os.close(write_fd)
            with self.subTest(suffix=suffix), self.assertRaises(ObservationError):
                collect_artifact(read_fd)
            self.assert_closed(read_fd)

    def test_read_fault_is_rejected_and_owned_descriptor_is_closed(self):
        read_fd, write_fd = self.pipe()
        os.write(write_fd, b"ready")
        with mock.patch("input_state_observer_collector.os.read",
                        side_effect=OSError(errno.EIO, "injected read fault")):
            with self.assertRaisesRegex(ObservationError, "read failed"):
                collect_artifact(read_fd)
        self.assert_closed(read_fd)
        os.close(write_fd)

    def test_partial_varint_and_payload_fail_at_eof(self):
        for partial in (b"\x80", b"\x05x"):
            read_fd, write_fd = self.pipe()
            os.write(write_fd, partial)
            os.close(write_fd)
            with self.subTest(partial=partial), self.assertRaises(ObservationError):
                collect_artifact(read_fd)
            self.assert_closed(read_fd)

    def test_huge_fd_is_rejected_without_closing_an_unrelated_descriptor(self):
        read_fd, write_fd = self.pipe()
        with self.assertRaisesRegex(ObservationError, "invalid owned descriptor"):
            collect_artifact(2**1000)
        self.assertTrue(fcntl.fcntl(read_fd, fcntl.F_GETFD) >= 0)
        os.close(read_fd)
        os.close(write_fd)

    def test_blocking_pipe_and_stream_socket_are_rejected_without_flag_changes(self):
        pipe_read, pipe_write = os.pipe()
        pipe_duplicate = os.dup(pipe_read)
        pipe_flags = fcntl.fcntl(pipe_duplicate, fcntl.F_GETFL)
        self.assertFalse(pipe_flags & os.O_NONBLOCK)
        with self.assertRaisesRegex(ObservationError, "descriptor must already be nonblocking"):
            collect_artifact(pipe_read)
        self.assert_closed(pipe_read)
        self.assertFalse(fcntl.fcntl(pipe_duplicate, fcntl.F_GETFL) & os.O_NONBLOCK)
        os.close(pipe_duplicate)
        os.close(pipe_write)

        receiver, sender = socket.socketpair()
        socket_fd = receiver.detach()
        socket_duplicate = os.dup(socket_fd)
        socket_flags = fcntl.fcntl(socket_duplicate, fcntl.F_GETFL)
        self.assertFalse(socket_flags & os.O_NONBLOCK)
        with self.assertRaisesRegex(ObservationError, "descriptor must already be nonblocking"):
            collect_artifact(socket_fd)
        self.assert_closed(socket_fd)
        self.assertFalse(fcntl.fcntl(socket_duplicate, fcntl.F_GETFL) & os.O_NONBLOCK)
        os.close(socket_duplicate)
        sender.close()

    def test_datagram_socket_is_rejected_and_owned_descriptor_is_closed(self):
        receiver, sender = socket.socketpair(type=socket.SOCK_DGRAM)
        receiver.setblocking(False)
        receiver_fd = receiver.detach()
        sender.close()
        with self.assertRaisesRegex(ObservationError, "stream type"):
            collect_artifact(receiver_fd)
        self.assert_closed(receiver_fd)

    def test_artifact_cap_is_hard_and_invalid_types_are_rejected(self):
        read_fd, write_fd = self.pipe()
        os.write(write_fd, self.artifact)
        os.close(write_fd)
        limits = Limits(max_record_bytes=4096, max_record_count=3,
                        max_receipts=32, max_artifact_bytes=8)
        with self.assertRaisesRegex(ObservationError, "byte limit"):
            collect_artifact(read_fd, limits=limits)
        self.assert_closed(read_fd)
        for value in (None, True, "3", -1):
            with self.subTest(value=value), self.assertRaises(ObservationError):
                collect_artifact(value)
        for value in (0, -1, 10.1, 10**1000, float("inf"), float("nan"), True):
            read_fd, write_fd = self.pipe()
            os.close(write_fd)
            with self.subTest(timeout=value), self.assertRaises(ObservationError):
                collect_artifact(read_fd, timeout_seconds=value)
            self.assert_closed(read_fd)

    def test_regular_file_invalid_fd_and_ownership_cleanup(self):
        file_fd = tempfile.TemporaryFile()
        regular_fd = os.dup(file_fd.fileno())
        with self.assertRaisesRegex(ObservationError, "pipe or stream socket"):
            collect_artifact(regular_fd)
        file_fd.close()
        self.assert_closed(regular_fd)
        invalid_fd = os.open(os.devnull, os.O_RDONLY)
        os.close(invalid_fd)
        with self.assertRaisesRegex(ObservationError, "inspection failed"):
            collect_artifact(invalid_fd)
        for descriptor in (invalid_fd,):
            self.assert_closed(descriptor)

if __name__ == "__main__":
    unittest.main()
