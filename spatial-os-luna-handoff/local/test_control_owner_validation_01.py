"""Strict protocol errors must precede application attribution on owner channels."""
import os
import unittest
import broker_pb2 as pb
import core_pb2 as core
from harness.wire import frame
from test_world_identity import receive
from test_control_owner_channels import ControlOwnerTests


class OwnerValidationTests(ControlOwnerTests):
    def check_validation_precedes_attribution(self, version, extra, expected):
        owner, world = self.register()
        application = pb.ApplicationRef()
        application.CopyFrom(self.app)
        application.server_id = os.urandom(32)
        self.assertNotEqual(application.server_id, self.server_id)
        request = pb.Request(protocol_version=version, request_id=101,
            control_acquire=pb.ApplicationControlRequest(principal=self.principal(), application=application))
        payload = request.SerializeToString()+extra
        world.sendall(len(payload).to_bytes(4, 'big')+payload)
        reply = receive(world.recv, world, pb.Reply)
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, 101)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, expected,
            f'Expected {core.ErrorCode.Name(expected)} before native attribution: {reply}')
        state = self.inspect(owner)
        self.assertFalse(state.channel_fenced)
        self.assertFalse(state.reservations)
        self.lease(world)

    def test_unsupported_version_precedes_native_attribution(self):
        self.check_validation_precedes_attribution(2, b'', core.UNSUPPORTED_VERSION)

    def test_unknown_fields_precede_native_attribution(self):
        self.check_validation_precedes_attribution(1, b'\xf8\x07\x01', core.INVALID_ARGUMENT)


if __name__ == '__main__':
    suite = unittest.TestSuite(OwnerValidationTests(name) for name in OwnerValidationTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
