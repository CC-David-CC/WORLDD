"""Crash witnesses against frozen production binaries, without clearing obligations."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import time
import unittest

from test_world_participant import WorldParticipantFixture
from test_workspace_participant import WorkspaceParticipantFixture


def record(test, value):
    folder = Path(os.environ['SPATIAL_CRASH_EVIDENCE'])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (test._testMethodName + '.json')).write_text(json.dumps(value, indent=2) + '\n')


class WorldCrashWitness(WorldParticipantFixture):
    def world(self, grants=(), native=False):
        self.assertTrue(native)
        world = self.start_world(native=True)
        world.management('system.hello')
        return world

    def start_display(self):
        # Own this diagnostic server directly. Never select an operator display.
        read_fd, write_fd = os.pipe()
        log = (self.root / 'crash-display.log').open('wb')
        server = subprocess.Popen([os.environ['SPATIAL_TEST_XSERVER'], '-displayfd', str(write_fd),
            '-screen', '0', '1280x720x24', '-nolisten', 'tcp', '-noreset', '-ac'],
            pass_fds=(write_fd,), stdout=log, stderr=log,
            env={**os.environ, 'LIBGL_ALWAYS_SOFTWARE': '1'})
        os.close(write_fd)
        def close():
            if server.poll() is None:
                server.send_signal(signal.SIGCONT)
                server.terminate()
                try: server.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    server.kill(); server.wait(timeout=3)
                    self.fail('Owned diagnostic display did not terminate')
            log.close()
        self.addCleanup(close)
        try:
            self.assertTrue(select.select([read_fd], [], [], 5)[0], 'No display readiness')
            notice = os.read(read_fd, 64)
        finally: os.close(read_fd)
        self.assertTrue(notice.endswith(b'\n') and notice[:-1].isdigit(), notice)
        before = {key: os.environ.get(key) for key in ('SPATIAL_TEST_DISPLAY', 'XAUTHORITY')}
        os.environ['SPATIAL_TEST_DISPLAY'] = ':' + notice.strip().decode()
        os.environ['XAUTHORITY'] = str(self.root / 'no-operator-authority')
        def restore():
            for key, value in before.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value
        self.addCleanup(restore)
        return server

    def test_world_exit_precedes_native_release_when_server_is_stopped(self):
        server = self.start_display()
        world, peer, probe = self.held()
        before = probe.input_events()
        self.assertTrue(any(e['event'] == 'key' and e['pressed'] for e in before))
        self.assertTrue(any(e['event'] == 'button' and e['pressed'] for e in before))
        children = Path(f'/proc/{world.proc.pid}/task/{world.proc.pid}/children').read_text().split()
        compositor = [int(pid) for pid in children if Path(f'/proc/{pid}/comm').read_text().strip() == 'spatialwm']
        self.assertEqual(len(compositor), 1)
        compositor_fd = os.pidfd_open(compositor[0]); self.addCleanup(os.close, compositor_fd)
        poll = select.poll(); poll.register(compositor_fd, select.POLLIN)
        try:
            server.send_signal(signal.SIGSTOP)
            self.until(lambda: Path(f'/proc/{server.pid}/status').read_text().split('State:', 1)[1].splitlines()[0],
                       lambda value: value.strip().startswith('T'))
            world.proc.kill(); self.assertEqual(world.proc.wait(timeout=3), -signal.SIGKILL)
            self.assertTrue(poll.poll(3000), 'Owned compositor remained alive after world exit')
            self.revoke_now()
            pending = self.barrier()
            self.assertEqual((pending.participant_count, pending.pending_count, pending.complete), (1, 1, False))
            with probe.condition:
                while_stopped = [dict(e) for e in probe.events if e['event'] in ('key', 'button')]
            self.assertFalse(any(not e['pressed'] for e in while_stopped), while_stopped)
        finally:
            if server.poll() is None: server.send_signal(signal.SIGCONT)
        after = self.until(probe.input_events, lambda events:
            any(e['event'] == 'key' and not e['pressed'] for e in events) and
            any(e['event'] == 'button' and not e['pressed'] for e in events))
        self.assertIsNone(probe.proc.poll())
        self.assertFalse(self.barrier().complete)
        world.close()
        replacement = self.start_world()
        self.assertEqual(replacement.proc.wait(timeout=4), 2)
        self.assertEqual(self.activations(), [(1, 0, 1)])
        record(self, dict(world_exit_confirmed=True, compositor_exit_confirmed=True,
            release_while_server_stopped=False, native_release_after_resume=True,
            pending_after_cleanup=self.barrier().pending_count, replacement_exit=replacement.proc.returncode,
            input_before=before, input_after=after, recovery_qualified=False))


class BrokerCrashWitness(WorkspaceParticipantFixture):
    def test_broker_crash_stops_detached_children_but_cannot_release_generation(self):
        item = self.start_broker(); item.call('system.hello')
        poll, count = self.launch_application()
        item.proc.kill(); self.assertEqual(item.proc.wait(timeout=3), -signal.SIGKILL)
        self.until(lambda: len(poll.poll(0)), lambda value: value == count)
        heartbeat = (self.project / 'heartbeat').read_text()
        self.revoke_now()
        pending = self.barrier()
        self.assertEqual((pending.participant_count, pending.pending_count, pending.complete), (1, 1, False))
        self.assertEqual((self.project / 'heartbeat').read_text(), heartbeat)
        item.close()
        replacement = self.start_broker()
        self.assertEqual(replacement.proc.wait(timeout=4), 2)
        self.assertEqual(self.activations(), [(1, 0, 1)])
        record(self, dict(broker_exit_confirmed=True, pinned_descendants_exited=count,
            pending_after_cleanup=self.barrier().pending_count, replacement_exit=replacement.proc.returncode,
            recovery_qualified=False))


if __name__ == '__main__': unittest.main(verbosity=2)
