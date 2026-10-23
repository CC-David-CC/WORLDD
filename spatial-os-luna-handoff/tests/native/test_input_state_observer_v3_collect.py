"""Real descriptor/deadline controls. No fixture is native routing evidence."""
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from google.protobuf.descriptor import FieldDescriptor
import input_state_observer_v3 as oracle
import input_state_observer_v3_collect as collector
import test_input_state_observer_v3 as controls


def live_fixture(cause=controls.pb.TARGET_DESTRUCTION):
    sample = controls.fixture(cause)
    start = time.monotonic_ns()
    def shift(message):
        for field, value in message.ListFields():
            if field.type == FieldDescriptor.TYPE_MESSAGE:
                if field.label == FieldDescriptor.LABEL_REPEATED:
                    for child in value:
                        shift(child)
                else:
                    shift(value)
            elif field.name.endswith("monotonic_ns") and value:
                setattr(message, field.name, start+2_000_000_000 if field.name == "deadline_monotonic_ns"
                        else start+value-100)
    shift(sample.arm)
    for record in sample.records:
        shift(record)
    return sample


def collect(fd, sample, **changes):
    args = dict(expected_session_id=controls.controls.SID, expected_arm_id=controls.controls.AID,
        expected_native_session_epoch=controls.controls.NATIVE, expected_arm=sample.arm,
        expected_bindings=sample.bindings, timeout_seconds=1)
    args.update(changes)
    return collector.collect(fd, **args)


class LifecycleCollectorTests(unittest.TestCase):
    def closed(self, fd):
        with self.assertRaises(OSError):
            os.fstat(fd)

    def stream(self, data):
        receiver, writer = socket.socketpair()
        fd = receiver.detach()
        try:
            writer.sendall(data)
            writer.shutdown(socket.SHUT_WR)
        finally:
            writer.close()
        return fd

    def test_three_lifecycle_streams_collect_through_eof_and_close(self):
        for cause in oracle.CAUSES:
            with self.subTest(cause=cause):
                sample = live_fixture(cause)
                fd = self.stream(controls.artifact(sample.records))
                self.assertEqual(collect(fd, sample), sample.press)
                self.closed(fd)

    def test_original_blocking_state_is_restored_on_surviving_alias(self):
        sample = live_fixture()
        fd = self.stream(controls.artifact(sample.records))
        alias = os.dup(fd)
        try:
            self.assertTrue(os.get_blocking(alias))
            self.assertEqual(collect(fd, sample), sample.press)
            self.assertTrue(os.get_blocking(alias))
            self.closed(fd)
        finally:
            os.close(alias)

    def test_complete_records_without_eof_still_expire(self):
        sample = live_fixture()
        receiver, writer = socket.socketpair()
        fd = receiver.detach()
        try:
            writer.sendall(controls.artifact(sample.records))
            with self.assertRaises(collector.ObservationCollectionError) as caught:
                collect(fd, sample, timeout_seconds=0.04)
            self.assertEqual(caught.exception.code, "DEADLINE_EXPIRED")
            self.closed(fd)
        finally:
            writer.close()

    def test_fragment_arrivals_do_not_reset_the_total_deadline(self):
        sample = live_fixture()
        receiver, writer = socket.socketpair()
        fd = receiver.detach()
        errors = []
        def trickle():
            try:
                for unused in range(40):
                    writer.sendall(b"x")
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except BaseException as error:
                errors.append(error)
            finally:
                writer.close()
        child = threading.Thread(target=trickle)
        child.start()
        start = time.monotonic()
        try:
            with self.assertRaises(collector.ObservationCollectionError) as caught:
                collect(fd, sample, timeout_seconds=0.08)
            self.assertEqual(caught.exception.code, "DEADLINE_EXPIRED")
            self.assertLess(time.monotonic()-start, 0.6)
            self.closed(fd)
        finally:
            child.join(timeout=1)
        self.assertFalse(child.is_alive())
        self.assertEqual(errors, [])

    def test_expired_arm_closes_owned_descriptor(self):
        sample = live_fixture()
        sample.arm.common.arm_monotonic_ns = 1
        sample.arm.common.deadline_monotonic_ns = 2
        receiver, writer = socket.socketpair()
        fd = receiver.detach()
        try:
            with self.assertRaises(collector.ObservationCollectionError) as caught:
                collect(fd, sample)
            self.assertEqual(caught.exception.code, "DEADLINE_EXPIRED")
            self.closed(fd)
        finally:
            writer.close()

    def test_truncated_artifact_is_rejected_and_closed(self):
        sample = live_fixture()
        fd = self.stream(controls.artifact(sample.records)[:-1])
        with self.assertRaises(oracle.ObservationV3Error) as caught:
            collect(fd, sample)
        self.assertEqual(caught.exception.code, "PROTOBUF_TRUNCATED")
        self.closed(fd)

    def test_oversized_stream_is_rejected_and_closed(self):
        sample = live_fixture()
        fd = self.stream(b"x"*(oracle.MAX_ARTIFACT_BYTES+1))
        with self.assertRaises(collector.ObservationCollectionError) as caught:
            collect(fd, sample)
        self.assertEqual(caught.exception.code, "ARTIFACT_BYTES_LIMIT")
        self.closed(fd)

    def test_regular_files_are_refused_without_closing_original_owner(self):
        sample = live_fixture()
        with tempfile.TemporaryFile() as original:
            fd = os.dup(original.fileno())
            with self.assertRaises(collector.ObservationCollectionError) as caught:
                collect(fd, sample)
            self.assertEqual(caught.exception.code, "FD_CLASS_INVALID")
            self.closed(fd)
            self.assertGreaterEqual(os.fstat(original.fileno()).st_ino, 0)

    def test_trusted_inputs_are_snapshotted_before_waiting_for_stream(self):
        sample = live_fixture()
        data = controls.artifact(sample.records)
        receiver, writer = socket.socketpair()
        fd = receiver.detach()
        entered, errors = threading.Event(), []
        original_selector = collector.selectors.DefaultSelector
        class SignallingSelector:
            def __init__(self):
                self.inner = original_selector()
            def register(self, *args):
                return self.inner.register(*args)
            def select(self, timeout):
                entered.set()
                return self.inner.select(timeout)
            def close(self):
                return self.inner.close()
        def emit():
            try:
                if not entered.wait(timeout=1):
                    raise RuntimeError("collector did not enter its wait")
                sample.arm.common.peer_actor_devices[0].native_object_epoch = 999
                sample.bindings[0].native_seat_id = 999
                writer.sendall(data)
                writer.shutdown(socket.SHUT_WR)
            except BaseException as error:
                errors.append(error)
            finally:
                writer.close()
        child = threading.Thread(target=emit)
        child.start()
        try:
            with patch.object(collector.selectors, "DefaultSelector", SignallingSelector):
                self.assertEqual(collect(fd, sample), sample.press)
            self.closed(fd)
        finally:
            child.join(timeout=1)
        self.assertFalse(child.is_alive())
        self.assertEqual(errors, [])

    def test_invalid_timeout_closes_owned_descriptor(self):
        for timeout in (0, -1, True, float("nan"), float("inf"), 11):
            with self.subTest(timeout=timeout):
                sample = live_fixture()
                fd = self.stream(b"")
                with self.assertRaises(collector.ObservationCollectionError) as caught:
                    collect(fd, sample, timeout_seconds=timeout)
                self.assertEqual(caught.exception.code, "TIMEOUT_INVALID")
                self.closed(fd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
