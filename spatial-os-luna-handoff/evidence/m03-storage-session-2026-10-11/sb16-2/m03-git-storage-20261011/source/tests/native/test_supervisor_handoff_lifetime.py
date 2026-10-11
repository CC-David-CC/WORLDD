"""Kernel EOF must follow the owned child's exit even while its supervisor is stopped."""
import os
from pathlib import Path
import select
import signal
import socket
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(SOURCE / 'tests/native'))
import test_authority_supervisor as supervisor


class HandoffTests(supervisor.AuthoritySupervisorTests):
    def child_exit_while_supervisor_stopped(self, proc, observed):
        child_pin = os.pidfd_open(self.authority_pid)
        supervisor_pin = os.pidfd_open(proc.pid)
        try:
            signal.pidfd_send_signal(supervisor_pin, signal.SIGSTOP)
            # SIGSTOP has no userspace acknowledgement. Wait for its kernel state.
            import time
            deadline = time.monotonic()+3
            while True:
                status = Path(f'/proc/{proc.pid}/status').read_text()
                state = next(line.split()[1] for line in status.splitlines() if line.startswith('State:'))
                if state in ('T', 't'):
                    break
                self.assertLess(time.monotonic(), deadline, 'Supervisor did not stop')
                time.sleep(.005)
            signal.pidfd_send_signal(child_pin, signal.SIGKILL)
            poller = select.poll()
            poller.register(child_pin, select.POLLIN)
            self.assertTrue(poller.poll(3000), 'Owned child exit was not observed')
            self.assertTrue(select.select([observed], [], [], .3)[0],
                'Supervisor retained an endpoint after its only child exited')
            self.assertEqual(os.read(observed.fileno(), 1), b'', 'Expected kernel EOF')
        finally:
            signal.pidfd_send_signal(supervisor_pin, signal.SIGCONT)
            os.close(child_pin)
            os.close(supervisor_pin)
        self.assertEqual(proc.wait(timeout=8), 2)
        self.checked_cleanup()

    def test_selected_endpoint_has_no_supervisor_copy_after_handoff(self):
        parent, child = socket.socketpair()
        self.addCleanup(parent.close)
        self.addCleanup(child.close)
        config = self.config()
        config.inherited_source_fds.append(child.fileno())
        config.launch.inherited_fds.append(38)
        original = supervisor.subprocess.Popen
        def spawn(*args, **kwargs):
            arguments = args[0] if args else kwargs['args']
            if Path(arguments[0]).name == 'spatial-authority-supervisor':
                kwargs['pass_fds'] = tuple(kwargs.get('pass_fds', ())) + (child.fileno(),)
            return original(*args, **kwargs)
        with patch.object(supervisor.subprocess, 'Popen', side_effect=spawn):
            proc, _ = self.running(config)
        self.activate_child(proc)
        child.close()
        self.assertFalse(select.select([parent], [], [], 0)[0], 'Endpoint closed before child exit')
        self.child_exit_while_supervisor_stopped(proc, parent)

    def test_child_stdout_has_no_supervisor_writer_after_handoff(self):
        proc, _ = self.running()
        self.activate_child(proc)
        self.assertFalse(select.select([proc.stdout], [], [], 0)[0], 'Unexpected child output')
        self.child_exit_while_supervisor_stopped(proc, proc.stdout)


if __name__ == '__main__':
    suite = unittest.TestSuite(HandoffTests(name) for name in HandoffTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
