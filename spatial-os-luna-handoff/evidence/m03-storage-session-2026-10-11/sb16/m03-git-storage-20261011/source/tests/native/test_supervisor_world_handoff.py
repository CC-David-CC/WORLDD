"""A real supervised world must start its compositor through a sealed native handoff."""
import os
from pathlib import Path
import select
import sys
import unittest
from unittest.mock import patch
from google.protobuf import text_format

SOURCE = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(SOURCE / 'tests/native'))
import core_pb2 as core
import launch_pb2 as launch
import process_scope_pb2 as process
import security_pb2 as security
import test_authority_supervisor as supervisor
from harness.wire import frame


class WorldNativeHandoffTests(supervisor.AuthoritySupervisorTests):
    def setUp(self):
        super().setUp()
        def preserve_logs():
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
                target.mkdir(parents=True, exist_ok=True)
                for index, path in enumerate(sorted(self.root.glob('supervisor-*.log'))):
                    (target / (self._testMethodName + '-supervisor-' + str(index) + '.log')).write_bytes(path.read_bytes())
        self.addCleanup(preserve_logs)

    def test_supervised_world_starts_real_compositor_with_owned_native_configuration(self):
        config = self.config()
        config.launch.CopyFrom(process.AuthorityProcessLaunch(
            executable=str(supervisor.owned.existing.PROBE.with_name('worldd')),
            arguments=['--state-dir', str(self.root / 'world'), '--native-security-fd', '38']))
        original_text = text_format.MessageToString
        original_native = supervisor.native_pb.NativeScopeConfig

        def text(value, *args, **kwargs):
            result = original_text(value, *args, **kwargs)
            if value.DESCRIPTOR.full_name == 'spatial.supervisor.v1.AuthoritySupervisorConfig':
                result += '\ncompositor_native_target_fd: 38\n'
            return result

        def native(*args, **kwargs):
            kwargs['role'] = security.COMPOSITOR
            return original_native(*args, **kwargs)

        with patch.object(text_format, 'MessageToString', side_effect=text), \
             patch.object(supervisor.native_pb, 'NativeScopeConfig', side_effect=native):
            proc, channel = self.supervisor_process(config)
        try:
            initial = self.message(channel, launch.AuthorityLaunchReply)
        except AssertionError as error:
            if proc.poll() is None:
                proc.wait(timeout=5)
            diagnostic = '\n'.join(path.read_text(errors='replace') for path in self.root.glob('supervisor-*.log'))
            self.fail('Supervised world native handoff failed: ' + str(error) + '\n' + diagnostic)
        self.assertTrue(initial.HasField('view'), initial)
        self.assertEqual(initial.view.record.stage, launch.START_ATTEMPTED)
        self.assertEqual(initial.view.record.native.role, security.COMPOSITOR)
        self.assertFalse(initial.view.HasField('ready'))
        proc.stdin.write(frame(core.Request(protocol_version=1, request_id=91,
            runtime_identities=core.RuntimeIdentitiesRequest())))
        proc.stdin.flush()
        reply = self.message(proc.stdout, core.Reply)
        self.assertEqual(reply.request_id, 91)
        self.assertTrue(reply.HasField('identities'), reply)
        ids = reply.identities
        self.assertGreater(ids.worldd_pid, 1)
        self.assertGreater(ids.spatialwm_pid, 1)
        self.assertNotEqual(ids.worldd_pid, ids.spatialwm_pid)
        self.assertEqual(ids.display, ':' + str(self.display))
        members = (self.cgroup / initial.view.record.process.scope_name / 'cgroup.procs').read_text().split()
        self.assertEqual(set(map(int, members)), {ids.worldd_pid, ids.spatialwm_pid})
        pins = [os.pidfd_open(pid) for pid in (ids.worldd_pid, ids.spatialwm_pid)]
        for pin in pins:
            self.addCleanup(os.close, pin)
            self.assertEqual(select.select([pin], [], [], 0)[0], [])
        stopped = self.control(channel, stop=launch.AuthorityLaunchQuery())
        self.assertEqual(stopped.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.checked_cleanup()
        for pin in pins:
            self.assertEqual(select.select([pin], [], [], 0)[0], [pin])


if __name__ == '__main__':
    suite = unittest.TestSuite(WorldNativeHandoffTests(name)
        for name in WorldNativeHandoffTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
