"""The session owns a real supervised broker and authenticates its transferred handle."""
import unittest
from unittest.mock import patch
import test_supervisor_broker_handoff_regressions as base
import broker_pb2 as broker
import core_pb2 as core
import issuer_pb2 as identity
import launch_pb2 as launch
import native_scope_pb2 as native
import security_pb2 as security
from harness.wire import frame


class SessionBrokerTests(base.BrokerHandoffRegressionTests):
    def start_session(self, config=None):
        binary = base.baseline.supervisor.owned.existing.PROBE.with_name('spatial-session-broker-probe')
        return self.supervisor_process(config or self.broker_config(), supervisor_binary=binary)

    def test_real_broker_root_and_administration_share_verified_live_child(self):
        proc, channel = self.start_session()
        initial = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(initial.HasField('view'), initial)
        self.assertEqual(initial.view.record.stage, launch.START_ATTEMPTED)
        self.assertEqual(initial.view.record, self.read_record())
        first = self.message(proc.stdout, core.Reply)
        self.assertTrue(first.HasField('hello'), first)
        self.assertEqual(first.hello.implementation, 'production')
        # A deliberately absent fixture owner must remain absent. This grants no world authority.
        owner = broker.ControlOwner(issuer_id=b'i' * 32,
            launch=identity.AuthorityFenceBegin(participant_id=b'p' * 32, registered_revision=1,
                generation=1, service_incarnation=b's' * 32, launch_id=b'l' * 32,
                supervision_revision=1, authorization_id=b'a' * 32),
            native=native.NativeScopeIdentity(launch_id=b'l' * 32, server_id=b'n' * 32,
                credential_id=b'c' * 32, role=security.COMPOSITOR))
        request = broker.ControlSupervisorRequest(protocol_version=1, request_id=81, inspect=owner)
        proc.stdin.write(frame(request)); proc.stdin.flush()
        reply = self.message(proc.stdout, broker.ControlSupervisorReply)
        self.assertEqual(reply.request_id, 81)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.NOT_FOUND)
        after = self.message(proc.stdout, core.Reply)
        self.assertEqual(after.hello.implementation, 'production')
        stopped = self.message(channel, launch.AuthorityLaunchReply)
        self.assertEqual(stopped.view.record.stage, launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=8), 0)
        self.assertEqual(self.checked_cleanup(), stopped.view.record)

    def test_coordinator_rejects_supplied_administrative_endpoint_arguments(self):
        config = self.broker_config()
        config.launch.arguments.extend(['--control-supervisor-fd', '70'])
        proc, _ = self.start_session(config)
        self.assertEqual(proc.wait(timeout=8), 2)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertEqual(self.activation_rows(), [])

    def test_failed_broker_exec_preserves_checked_owned_cleanup(self):
        config = self.broker_config(); config.launch.executable = '/nonexistent-session-broker'
        proc, _ = self.start_session(config)
        self.assertEqual(proc.wait(timeout=8), 2)
        self.checked_cleanup()
        self.assertEqual(self.activation_rows(), [])

    def test_wrong_native_role_cannot_supply_a_broker_handle(self):
        original = base.baseline.supervisor.native_pb.NativeScopeConfig
        def compositor(*args, **kwargs):
            kwargs['role'] = security.COMPOSITOR
            return original(*args, **kwargs)
        with patch.object(base.baseline.supervisor.native_pb, 'NativeScopeConfig', side_effect=compositor):
            proc, _ = self.start_session()
        self.assertEqual(proc.wait(timeout=8), 2)
        # The supervisor validates the sealed role before it creates launch ownership.
        diagnostic = '\n'.join(p.read_text(errors='replace') for p in self.root.glob('supervisor-*.log'))
        self.assertIn('Broker handoff requires a broker native scope', diagnostic)
        self.assertFalse((self.journal / 'launch.db').exists())
        self.assertFalse(self.child_config.exists())
        self.assertEqual(self.activation_rows(), [])


if __name__ == '__main__':
    suite = unittest.TestSuite(SessionBrokerTests(name) for name in SessionBrokerTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
