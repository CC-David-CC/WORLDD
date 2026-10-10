"""Derive control owners from retained real supervisor launches, without caller-selected identities."""
from pathlib import Path
import sqlite3
import struct
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(SOURCE / 'tests/native'))
import test_authority_supervisor as supervisor
import launch_pb2 as pb
import security_pb2 as security


def query_bytes(request_id, payload=b''):
    # Empty additive operation 21 also runs against the baseline generated schema.
    assert len(payload) < 128
    return (pb.AuthorityLaunchRequest(protocol_version=1, request_id=request_id).SerializeToString()
            + b'\xaa\x01' + bytes([len(payload)]) + payload)


def assert_owner(test, owner, record):
    test.assertEqual(owner.issuer_id, record.binding.issuer_id)
    test.assertEqual(owner.native, record.native)
    test.assertEqual(owner.native.role, security.COMPOSITOR)
    expected = dict(participant_id=record.binding.participant_id,
        registered_revision=record.binding.registered_revision,
        generation=record.authorization.request.previous_generation + 1,
        service_incarnation=record.binding.service_incarnation,
        launch_id=record.process.launch_id,
        supervision_revision=record.authorization.request.supervision_revision,
        authorization_id=record.authorization.authorization_id)
    for name, value in expected.items():
        test.assertEqual(getattr(owner.launch, name), value, name)
    test.assertEqual(owner.native.launch_id, owner.launch.launch_id)


class BindingTests(supervisor.owned.OwnedParticipantTests):
    def probe(self, *args, **kwargs):
        original = supervisor.native_pb.NativeScopeConfig
        def compositor(*args, **kwargs):
            kwargs['role'] = security.COMPOSITOR
            return original(*args, **kwargs)
        with patch.object(supervisor.native_pb, 'NativeScopeConfig', side_effect=compositor):
            return super().probe(*args, **kwargs)

    def owner_reply(self, proc, payload=b''):
        data = query_bytes(55, payload)
        proc.stdin.write(struct.pack('!I', len(data)) + data)
        proc.stdin.flush()
        return self.reply(proc)

    def owner(self, proc):
        reply = self.owner_reply(proc)
        self.assertFalse(reply.HasField('error'), 'Missing owned control binding: ' + str(reply))
        self.assertTrue(reply.HasField('control_owner'), reply)
        return reply.control_owner

    def test_prepared_owner_matches_activation_and_exact_issuer_fence(self):
        proc, _, _, batch = self.connected()
        self.assertTrue(self.owner_reply(proc).HasField('error'))
        approved = self.authorize(proc)
        self.assertTrue(approved.HasField('view'), approved)
        before = self.read_record()
        owner = self.owner(proc)
        assert_owner(self, owner, before)
        self.assertEqual(self.owner(proc), owner)
        self.assertEqual(self.read_record(), before)
        self.assertEqual(self.activation_rows(), [])
        self.assertTrue(self.run_child(proc, 'import time;time.sleep(30)').HasField('view'))
        self.assertTrue(self.activate(proc, batch.through_revision).HasField('view'))
        active = self.update(proc, 'activated').activated
        self.assertEqual(owner.launch.generation, active.generation)
        self.assertEqual(self.owner(proc), owner)
        fenced = self.call(proc, fence=pb.AuthorityLaunchQuery())
        self.assertTrue(fenced.HasField('view'), fenced)
        for name in ('generation', 'service_incarnation', 'launch_id', 'supervision_revision', 'authorization_id'):
            self.assertEqual(getattr(fenced.view.record.fence, name), getattr(owner.launch, name))
        self.assertTrue(self.owner_reply(proc).HasField('error'))
        self.stopped(proc)
        self.assertTrue(self.owner_reply(proc).HasField('error'))

    def test_reopened_authorized_record_cannot_reconstruct_control_owner(self):
        proc, _, _, _ = self.connected()
        self.assertTrue(self.authorize(proc).HasField('view'))
        self.owner(proc)
        before = self.read_record()
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=5), 0)
        reopened = self.probe(start_server=False)
        self.assertEqual(self.call(reopened, inspect=pb.AuthorityLaunchQuery()).view.record, before)
        self.assertTrue(self.owner_reply(reopened).HasField('error'))
        self.assertEqual(self.read_record(), before)

    def test_failed_authorization_commit_cannot_expose_owner(self):
        proc, _, _, _ = self.connected()
        before = self.read_record()
        with sqlite3.connect(self.journal / 'launch.db') as db:
            db.execute("CREATE TRIGGER deny_owner BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'owner journal fault'); END")
        self.assertTrue(self.authorize(proc).HasField('error'))
        self.assertTrue(self.owner_reply(proc).HasField('error'))
        self.assertEqual(self.read_record(), before)
        with sqlite3.connect(self.journal / 'launch.db') as db:
            db.execute('DROP TRIGGER deny_owner')
        self.assertTrue(self.authorize(proc).HasField('view'))
        assert_owner(self, self.owner(proc), self.read_record())
        self.stopped(proc)

    def test_query_rejects_nested_unknown_owner_selector_without_mutation(self):
        proc, _, _, _ = self.connected()
        self.assertTrue(self.authorize(proc).HasField('view'))
        before = self.read_record()
        self.assertTrue(self.owner_reply(proc, b'\xf8\x07\x01').HasField('error'))
        self.assertEqual(self.read_record(), before)
        assert_owner(self, self.owner(proc), before)
        self.stopped(proc)


class BrokerBindingTests(supervisor.owned.OwnedParticipantTests):
    def test_broker_native_role_cannot_become_world_control_owner(self):
        proc, _, _, _ = self.connected()
        self.assertTrue(self.authorize(proc).HasField('view'))
        before = self.read_record()
        data = query_bytes(55)
        proc.stdin.write(struct.pack('!I', len(data)) + data)
        proc.stdin.flush()
        reply = self.reply(proc)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(self.read_record(), before)
        self.stopped(proc)


class SupervisorBindingTests(supervisor.AuthoritySupervisorTests):
    def test_production_supervisor_returns_exact_owned_binding_without_stopping_child(self):
        original = supervisor.native_pb.NativeScopeConfig
        def compositor(*args, **kwargs):
            kwargs['role'] = security.COMPOSITOR
            return original(*args, **kwargs)
        with patch.object(supervisor.native_pb, 'NativeScopeConfig', side_effect=compositor):
            proc, channel = self.running()
        active = self.activate_child(proc)
        before = self.read_record()
        data = query_bytes(77)
        channel.sendall(struct.pack('!I', len(data)) + data)
        reply = self.message(channel, pb.AuthorityLaunchReply)
        self.assertEqual(reply.request_id, 77)
        self.assertFalse(reply.HasField('error'), 'Missing production control binding: ' + str(reply))
        self.assertTrue(reply.HasField('control_owner'), reply)
        assert_owner(self, reply.control_owner, before)
        self.assertEqual(reply.control_owner.launch.generation, active.generation)
        self.assertEqual(self.read_record(), before)
        self.assertIsNone(proc.poll())
        self.assertTrue(self.control(channel, fence=pb.AuthorityLaunchQuery()).HasField('view'))
        self.assertEqual(proc.wait(timeout=5), 0)
        self.checked_cleanup()


if __name__ == '__main__':
    suite = unittest.TestSuite(cls(name) for cls in (BindingTests, BrokerBindingTests, SupervisorBindingTests)
        for name in cls.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
