"""A real supervised broker must receive its owner's sealed native credential."""
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
from google.protobuf import text_format
SOURCE = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(SOURCE / 'tests/native'))
import broker_pb2 as broker
import core_pb2 as core
import launch_pb2 as launch
import process_scope_pb2 as process
import test_authority_supervisor as supervisor
from harness.wire import frame


class BrokerHandoffTests(supervisor.AuthoritySupervisorTests):
    def test_supervised_real_broker_receives_native_configuration(self):
        config = self.config()
        executable = supervisor.owned.existing.PROBE.with_name('workspaced')
        config.launch.CopyFrom(process.AuthorityProcessLaunch(
            executable=str(executable), arguments=[
                '--state-dir', str(self.root / 'execution'), '--native-security-fd', '38']))
        original = text_format.MessageToString

        def encode(value, *args, **kwargs):
            text = original(value, *args, **kwargs)
            if value.DESCRIPTOR.full_name == 'spatial.supervisor.v1.AuthoritySupervisorConfig':
                # Text input exercises the actual production parser, including the old schema.
                text += '\nbroker_native_target_fd: 38\n'
            return text

        with patch.object(text_format, 'MessageToString', side_effect=encode):
            proc, channel = self.supervisor_process(config)
        try:
            initial = self.message(channel, launch.AuthorityLaunchReply)
        except AssertionError as error:
            if proc.poll() is None:
                proc.wait(timeout=5)
            diagnostic = '\n'.join(path.read_text(errors='replace')
                for path in sorted(self.root.glob('supervisor-*.log')))
            self.fail('Supervised broker native handoff failed: ' + str(error) + '\n' + diagnostic)
        self.assertTrue(initial.HasField('view'), initial)
        self.assertEqual(initial.view.record.stage, launch.START_ATTEMPTED)
        request = broker.Request(protocol_version=1, request_id=91,
            management=core.Request(protocol_version=1, request_id=91, hello=core.HelloRequest()))
        proc.stdin.write(frame(request)); proc.stdin.flush()
        reply = self.message(proc.stdout, broker.Reply)
        self.assertEqual(reply.request_id, 91)
        self.assertTrue(reply.HasField('management'), reply)
        self.assertTrue(reply.management.HasField('hello'), reply)
        self.assertEqual(reply.management.hello.implementation, 'production')
        self.assertIsNone(proc.poll())
        stopped = self.control(channel, stop=launch.AuthorityLaunchQuery())
        self.assertEqual(stopped.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.checked_cleanup()


if __name__ == '__main__':
    suite = unittest.TestSuite(BrokerHandoffTests(name)
        for name in BrokerHandoffTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
