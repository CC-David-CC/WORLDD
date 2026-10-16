from pathlib import Path
import importlib.util
import os
import signal
import time
import unittest
from unittest import mock


def load_fixture(environment, module_name):
    value = os.environ.get(environment)
    if not value:
        raise RuntimeError(f"Set {environment} to an explicit actor_transition_crosshost_host.py")
    path = Path(value).resolve(strict=True)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import fixture from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load_fixture("M06_CROSSHOST_HOST_FIXTURE", "actor_transition_crosshost_host_under_test")
baseline = load_fixture("M06_CROSSHOST_HOST_BASELINE_FIXTURE", "actor_transition_crosshost_host_baseline")


class PidfdGuardTests(unittest.TestCase):
    def setUp(self):
        self.children = []

    def tearDown(self):
        for pid, writer in self.children:
            try:
                os.write(writer, b"x")
            except OSError:
                pass
            try:
                done, _ = os.waitpid(pid, os.WNOHANG)
                if done == 0:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
            except (ChildProcessError, ProcessLookupError):
                pass
            try:
                os.close(writer)
            except OSError:
                pass

    def child(self):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(write_fd)
                os.read(read_fd, 1)
            finally:
                os._exit(0)
        os.close(read_fd)
        self.children.append((pid, write_fd))
        return pid, write_fd

    @staticmethod
    def state(pid):
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(") ", 1)[1].split()
        return fields[0]

    def wait_zombie(self, pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                if self.state(pid) == "Z":
                    return
            except (FileNotFoundError, ProcessLookupError):
                break
            time.sleep(0.005)
        self.fail("child did not reach the retained zombie state")

    @staticmethod
    def tracked_fd_ops(module):
        opened, closed = [], []
        real_open, real_close = module.os.pidfd_open, module.os.close

        def open_fd(pid):
            fd = real_open(pid)
            opened.append(fd)
            return fd

        def close_fd(fd):
            if fd in opened:
                closed.append(fd)
            return real_close(fd)

        return opened, closed, open_fd, close_fd

    def assert_closed_once(self, opened, closed):
        self.assertEqual(len(opened), 1)
        self.assertEqual(closed, opened)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def fixture_root(self, owner_record, world_record):
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="spatial-m06-run03-owner-cleanup-", dir="/tmp"))
        root.chmod(0o700)
        marker = root.stat()
        token = "owner-cleanup-regression"
        host.atomic_json(root / "owner.json", dict(token=token, device=marker.st_dev,
                          inode=marker.st_ino, uid=os.getuid()))
        host.atomic_json(root / "launch.json", dict(session="retained-dead-pane",
                          pane_pid=owner_record["pid"], owner=owner_record))
        host.atomic_json(root / "process.json", dict(owner=owner_record, worldd=world_record))
        return root, token

    def zombie(self):
        pid, writer = self.child()
        record = host.identity(pid)
        os.write(writer, b"x")
        self.wait_zombie(pid)
        return record

    def test_exited_owner_with_retained_dead_tmux_pane_finishes_from_pidfd(self):
        owner, worldd = self.zombie(), self.zombie()
        root, token = self.fixture_root(owner, worldd)
        try:
            with mock.patch.object(host.subprocess, "run") as tmux, mock.patch.object(host, "request_stop_once") as stop_request:
                host.stop_owner(root, token)
            tmux.assert_not_called()
            stop_request.assert_not_called()
            self.assertFalse((root / "cancel").exists())
        finally:
            import shutil
            shutil.rmtree(root)

    def test_teardown_acquires_owner_identity_before_cancel_and_observes_exit(self):
        pid, writer = self.child()
        owner = host.identity(pid)
        worldd = self.zombie()
        root, token = self.fixture_root(owner, worldd)
        original_guard = host.guarded_pidfd
        order = []

        def guarded(record):
            if record["pid"] == owner["pid"]:
                self.assertFalse((root / "cancel").exists())
                order.append("identity-pinned")
            return original_guard(record)

        def stop_once(*_):
            order.append("stop-request")
            os.write(writer, b"x")
            return True

        try:
            with mock.patch.object(host, "guarded_pidfd", side_effect=guarded), mock.patch.object(host, "request_stop_once", side_effect=stop_once):
                host.stop_owner(root, token)
            self.assertEqual(order[0], "identity-pinned")
            self.assertIn("stop-request", order)
        finally:
            import shutil
            shutil.rmtree(root)

    def test_actual_live_owner_timeout_retains_root(self):
        pid, writer = self.child()
        owner = host.identity(pid)
        worldd = self.zombie()
        root, token = self.fixture_root(owner, worldd)
        ticks = iter((0.0, 21.0, 22.0, 23.0))
        try:
            with mock.patch.object(host, "request_stop_once", return_value=False), mock.patch.object(host.time, "monotonic", side_effect=lambda: next(ticks, 24.0)), mock.patch.object(host.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "Owner still running"):
                    host.stop_owner(root, token)
            self.assertTrue(root.exists())
        finally:
            import shutil
            shutil.rmtree(root)
            os.write(writer, b"x")

    def test_wrong_live_pid_identity_refuses_cancellation(self):
        pid, writer = self.child()
        owner = host.identity(pid)
        worldd = self.zombie()
        root, token = self.fixture_root(owner, worldd)
        try:
            with mock.patch.object(host, "guarded_pidfd", side_effect=RuntimeError("PID/start/executable/command ownership mismatch")):
                with self.assertRaisesRegex(RuntimeError, "ownership mismatch"):
                    host.stop_owner(root, token)
            self.assertFalse((root / "cancel").exists())
        finally:
            import shutil
            shutil.rmtree(root)
            os.write(writer, b"x")

    def test_matching_live_process_keeps_guarded_pidfd_open(self):
        pid, writer = self.child()
        record = host.identity(pid)
        fd = host.guarded_pidfd(record)
        try:
            os.fstat(fd)
        finally:
            os.close(fd)
            os.write(writer, b"x")
            os.waitpid(pid, 0)

    def test_live_changed_identity_is_rejected_and_descriptor_closed_once(self):
        pid, writer = self.child()
        record = host.identity(pid)
        changed = dict(record, start_ticks=record["start_ticks"] + 1)
        opened, closed, open_fd, close_fd = self.tracked_fd_ops(host)
        with (
            mock.patch.object(host.os, "pidfd_open", side_effect=open_fd),
            mock.patch.object(host.os, "close", side_effect=close_fd),
            mock.patch.object(host, "identity", return_value=changed),
        ):
            with self.assertRaisesRegex(RuntimeError, "ownership mismatch"):
                host.guarded_pidfd(record)
        self.assert_closed_once(opened, closed)
        os.write(writer, b"x")
        os.waitpid(pid, 0)

    def test_exiting_child_distinguishes_original_failure_from_pidfd_fix(self):
        for module, expected in ((baseline, RuntimeError), (host, ProcessLookupError)):
            with self.subTest(module=module.__name__):
                pid, writer = self.child()
                record = module.identity(pid)
                opened, closed, open_fd, close_fd = self.tracked_fd_ops(module)

                def exit_during_identity(target_pid):
                    self.assertEqual(target_pid, pid)
                    os.write(writer, b"x")
                    self.wait_zombie(pid)
                    return dict(record, command=[])

                with (
                    mock.patch.object(module.os, "pidfd_open", side_effect=open_fd),
                    mock.patch.object(module.os, "close", side_effect=close_fd),
                    mock.patch.object(module, "identity", side_effect=exit_during_identity),
                ):
                    with self.assertRaises(expected):
                        module.guarded_pidfd(record)
                self.assert_closed_once(opened, closed)
                os.waitpid(pid, 0)

    def test_identity_disappearance_without_pidfd_readiness_is_not_gone(self):
        pid, writer = self.child()
        record = host.identity(pid)
        opened, closed, open_fd, close_fd = self.tracked_fd_ops(host)
        with (
            mock.patch.object(host.os, "pidfd_open", side_effect=open_fd),
            mock.patch.object(host.os, "close", side_effect=close_fd),
            mock.patch.object(host, "identity", side_effect=FileNotFoundError("transient proc read")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Could not verify"):
                host.guarded_pidfd(record)
        self.assert_closed_once(opened, closed)
        os.write(writer, b"x")
        os.waitpid(pid, 0)

    def test_pidfd_error_or_invalid_poll_event_is_not_exit_proof(self):
        pid, writer = self.child()
        record = host.identity(pid)
        opened, closed, open_fd, close_fd = self.tracked_fd_ops(host)
        for event in (host.select.POLLERR, host.select.POLLNVAL):
            poller = mock.Mock()
            poller.poll.return_value = [(99, event)]
            with self.subTest(event=event), mock.patch.object(host.os, "pidfd_open", side_effect=open_fd), mock.patch.object(host.os, "close", side_effect=close_fd), mock.patch.object(host, "identity", return_value=dict(record, command=[])), mock.patch.object(host.select, "poll", return_value=poller):
                with self.assertRaisesRegex(RuntimeError, "ownership mismatch"):
                    host.guarded_pidfd(record)
        self.assertEqual(len(opened), 2)
        self.assertEqual(closed, opened)
        for fd in opened:
            with self.assertRaises(OSError):
                os.fstat(fd)
        os.write(writer, b"x")
        os.waitpid(pid, 0)


if __name__ == "__main__":
    unittest.main()
