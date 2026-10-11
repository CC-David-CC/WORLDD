"""Counterexamples for the production control-reply validator, not transport authentication."""
from pathlib import Path
import copy
import struct
import subprocess
import sys
import unittest
import broker_pb2 as pb
import core_pb2 as core
import security_pb2 as security

PROBE = Path(sys.argv.pop(1)).resolve()


def frame(message):
    data = message.SerializeToString()
    return struct.pack('!I', len(data)) + data


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.owner = pb.ControlOwner(issuer_id=b'i'*32)
        self.owner.launch.participant_id = b'p'*32
        self.owner.launch.registered_revision = 1
        self.owner.launch.generation = 2
        self.owner.launch.service_incarnation = b's'*32
        self.owner.launch.launch_id = b'l'*32
        self.owner.launch.supervision_revision = 3
        self.owner.launch.authorization_id = b'a'*32
        self.owner.native.launch_id = b'l'*32
        self.owner.native.server_id = b'n'*32
        self.owner.native.credential_id = b'c'*32
        self.owner.native.role = security.COMPOSITOR
        self.request = pb.ControlSupervisorRequest(protocol_version=1, request_id=71, inspect=self.owner)
        self.reply = pb.ControlSupervisorReply(protocol_version=1, request_id=71)
        self.reply.state.owner.CopyFrom(self.owner)

    def reservation(self, identifier=b'r'*32, application=b'editor'):
        value = self.reply.state.reservations.add(id=identifier)
        value.principal.issuer_id = b'i'*32
        value.principal.actor_id = b'u'*32
        value.application.workspace = b'project'
        value.application.application = application
        value.application.server_id = b'n'*32
        return value

    def check(self, accepted):
        result = subprocess.run([str(PROBE)], input=frame(self.request)+frame(self.reply), capture_output=True, timeout=10)
        for marker in (b'AddressSanitizer', b'LeakSanitizer', b'runtime error:'):
            self.assertNotIn(marker, result.stderr)
        self.assertEqual(result.returncode, 0 if accepted else 2, result.stderr.decode(errors='replace'))
        if accepted:
            self.assertEqual(result.stdout, frame(self.reply))
        else:
            self.assertEqual(result.stdout, b'')
            self.assertIn(b'control reply rejected:', result.stderr)

    def test_each_success_operation_preserves_exact_reply(self):
        for operation in ('register', 'inspect', 'fence'):
            with self.subTest(operation=operation):
                self.request.ClearField('operation')
                getattr(self.request, operation).CopyFrom(self.owner)
                self.reply.state.channel_fenced = operation == 'fence'
                self.check(True)

    def test_typed_remote_error_is_preserved_as_error(self):
        self.reply.error.code = core.NOT_FOUND
        self.reply.error.message = 'Exact owner absent'
        self.check(True)

    def test_zero_and_unknown_error_codes_are_rejected(self):
        for code in (0, 9999):
            with self.subTest(code=code):
                self.reply.error.code = code
                self.check(False)

    def test_wrong_request_id_and_version_are_rejected(self):
        self.reply.request_id += 1
        self.check(False)
        self.reply.request_id = self.request.request_id
        self.reply.protocol_version = 2
        self.check(False)
        self.reply.protocol_version = 1
        self.request.protocol_version = 2
        self.check(False)

    def test_missing_request_or_reply_operation_is_rejected(self):
        saved = copy.deepcopy(self.request)
        self.request.ClearField('operation')
        self.check(False)
        self.request.CopyFrom(saved)
        self.reply.ClearField('outcome')
        self.check(False)

    def test_owner_substitution_is_rejected(self):
        for path in ('issuer_id', 'launch.generation', 'launch.authorization_id', 'native.credential_id', 'native.server_id'):
            with self.subTest(path=path):
                self.reply.state.owner.CopyFrom(self.owner)
                target = self.reply.state.owner
                parts = path.split('.')
                for part in parts[:-1]:
                    target = getattr(target, part)
                value = getattr(target, parts[-1])
                setattr(target, parts[-1], value+1 if isinstance(value, int) else b'x'*32)
                self.check(False)

    def test_matching_but_invalid_owner_cannot_validate_reply(self):
        self.request.inspect.native.launch_id = b'x'*32
        self.reply.state.owner.CopyFrom(self.request.inspect)
        self.check(False)

    def test_fence_requires_fenced_acknowledgement(self):
        self.request.fence.CopyFrom(self.owner)
        self.check(False)
        self.reply.state.channel_fenced = True
        self.check(True)

    def test_registration_cannot_report_existing_reservations_or_fencing(self):
        getattr(self.request, 'register').CopyFrom(self.owner)
        self.reservation()
        self.check(False)
        self.reply.state.ClearField('reservations')
        self.reply.state.channel_fenced = True
        self.check(False)

    def test_inspection_preserves_multiple_principals_and_fenced_reservations(self):
        self.reservation()
        other = self.reservation(b'v'*32, b'terminal')
        other.principal.actor_id = b'z'*32
        # Owner authority and actor identity are separate protocol concepts.
        other.principal.issuer_id = b'd'*32
        self.reply.state.channel_fenced = True
        self.check(True)

    def test_malformed_reservation_fields_are_rejected(self):
        original = copy.deepcopy(self.reservation())
        for path in ('id', 'principal.issuer_id', 'principal.actor_id', 'application.workspace', 'application.application', 'application.server_id'):
            with self.subTest(path=path):
                value = self.reply.state.reservations[0]
                value.CopyFrom(original)
                parts = path.split('.')
                for part in parts[:-1]:
                    value = getattr(value, part)
                setattr(value, parts[-1], b'')
                self.check(False)

    def test_wrong_native_server_is_rejected(self):
        self.reservation().application.server_id = b'x'*32
        self.check(False)

    def test_duplicate_reservation_or_application_is_rejected(self):
        self.reservation()
        self.reservation(application=b'terminal')
        self.check(False)
        self.reply.state.reservations[1].id = b'v'*32
        self.reply.state.reservations[1].application.application = b'editor'
        self.check(False)

    def test_nested_unknown_fields_are_rejected(self):
        self.reservation().principal.MergeFromString(b'\xf8\x07\x01')
        self.check(False)

    def test_reservation_count_and_snapshot_bounds_are_enforced(self):
        for index in range(257):
            self.reservation(index.to_bytes(32, 'big'), str(index).encode())
        self.check(False)
        self.reply.state.ClearField('reservations')
        self.reservation(application=b'a'*60000)
        self.check(False)

    def test_packet_limit_applies_to_error_replies(self):
        self.reply.error.code = core.INTERNAL_ERROR
        self.reply.error.message = 'x'*65536
        self.check(False)

    def recovery(self):
        cleanup = pb.ControlOwnerRecovery(owner=self.owner)
        cleanup.native_stopped.identity.CopyFrom(self.owner.native)
        value = cleanup.process_stopped.identity
        value.launch_id = self.owner.launch.launch_id
        value.scope_name = 'authority-' + value.launch_id.hex()
        value.boot_id = '12345678-1234-1234-1234-123456789abc'; value.device = 23; value.inode = 42
        self.request.recover.CopyFrom(cleanup)
        self.reply.state.channel_fenced = True
        self.reply.state.recovery.cleanup.CopyFrom(cleanup)
        self.reservation()
        self.reply.state.recovery.released.extend(self.reply.state.reservations)
        self.reply.state.ClearField('reservations')

    def test_recovery_and_later_inspection_preserve_exact_receipt(self):
        self.recovery(); self.check(True)
        self.request.inspect.CopyFrom(self.owner); self.check(True)
        self.request.fence.CopyFrom(self.owner); self.check(True)

    def test_recovery_requires_matching_complete_receipt(self):
        self.recovery(); original = copy.deepcopy(self.reply)
        for field in ('recovery', 'channel_fenced'):
            self.reply.CopyFrom(original); self.reply.state.ClearField(field); self.check(False)
        self.reply.CopyFrom(original); self.reply.state.recovery.cleanup.process_stopped.identity.inode += 1
        self.check(False)
        self.reply.CopyFrom(original); self.request.recover.ClearField('native_stopped'); self.check(False)

    def test_recovered_owner_cannot_have_active_reservations(self):
        self.recovery(); self.reservation(); self.check(False)

    def test_recovered_reservations_receive_same_identity_and_size_validation(self):
        self.recovery(); original = copy.deepcopy(self.reply)
        self.reply.state.recovery.released[0].application.server_id = b'x'*32; self.check(False)
        self.reply.CopyFrom(original)
        self.reply.state.recovery.released.add().CopyFrom(self.reply.state.recovery.released[0]); self.check(False)
        self.reply.CopyFrom(original)
        self.reply.state.recovery.released[0].application.application = b'x'*60000; self.check(False)

    def test_recovery_inspection_rejects_changed_owner_or_cleanup(self):
        self.recovery(); self.request.inspect.CopyFrom(self.owner)
        original = copy.deepcopy(self.reply)
        self.reply.state.recovery.cleanup.owner.launch.generation += 1; self.check(False)
        self.reply.CopyFrom(original)
        self.reply.state.recovery.cleanup.native_stopped.identity.credential_id = b'x'*32; self.check(False)


if __name__ == '__main__':
    unittest.main()
