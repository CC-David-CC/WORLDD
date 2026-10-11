"""An owned crash must fence the exact issuer generation without retiring obligations."""
import os
from pathlib import Path
import signal
import sys
import unittest

SOURCE = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(SOURCE/'tests/native'))
import test_authority_supervisor as supervisor
import launch_pb2 as launch


class SupervisorCrashFencingTests(supervisor.AuthoritySupervisorTests):
    def activated_with_pending_obligation(self):
        proc, channel = self.running()
        active = self.activate_child(proc)
        self.revoke()
        self.child_update(proc, 'revocations')
        self.assertEqual(self.barrier().pending_count, 1)
        return proc, channel, active, self.read_record(), self.activation_rows()

    def assert_exact_crash_fence(self, proc, active, before, activations):
        self.assertEqual(proc.wait(timeout=8), 2)
        record = self.checked_cleanup()
        self.assertEqual(self.activation_rows(), activations)
        barrier = self.barrier()
        self.assertEqual((barrier.participant_count, barrier.pending_count, barrier.complete), (1, 1, False))
        self.assertTrue(record.HasField('fence'), 'Missing automatic supervisor crash fence')
        fence = record.fence
        self.assertEqual(fence.generation, active.generation)
        self.assertEqual(fence.registration, self.issuer.registration)
        self.assertEqual(fence.launch_id, before.process.launch_id)
        self.assertEqual(fence.authorization_id, before.authorization.authorization_id)
        self.assertEqual(fence.service_incarnation, before.binding.service_incarnation)
        self.assertEqual(fence.supervisor_public_key, self.issuer.supervisor_public)
        self.assertEqual(fence.security_revision, 1)
        self.assertEqual(record.authorization, before.authorization)
        self.assertEqual(len(self.issuer.snapshot_all()['authority_fence']), 1)

    def test_child_crash_fences_exact_generation_and_preserves_obligation(self):
        proc, channel, active, before, activations = self.activated_with_pending_obligation()
        os.kill(self.authority_pid, signal.SIGKILL)
        self.assertTrue(self.message(channel, launch.AuthorityLaunchReply).HasField('error'))
        self.assert_exact_crash_fence(proc, active, before, activations)

    def test_management_loss_fences_exact_generation_and_preserves_obligation(self):
        proc, channel, active, before, activations = self.activated_with_pending_obligation()
        channel.close()
        self.assert_exact_crash_fence(proc, active, before, activations)


import sqlite3
import security_pb2 as security
import issuer_pb2 as identity


class SupervisorCrashFailureTests(SupervisorCrashFencingTests):
    def fail_child(self, proc, channel):
        os.kill(self.authority_pid, signal.SIGKILL)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(proc.wait(timeout=8), 2)
        return reply

    def test_termination_signal_fences_activated_service(self):
        proc, channel, active, before, activations = self.activated_with_pending_obligation()
        proc.send_signal(signal.SIGTERM)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertIn('termination requested', reply.error.message)
        self.assert_exact_crash_fence(proc, active, before, activations)

    def test_unconsumed_start_is_cleaned_without_fabricated_fence(self):
        proc, channel = self.running()
        self.fail_child(proc, channel)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        rows = self.issuer.snapshot_all()
        self.assertEqual(rows['authority_fence'], [])
        self.assertEqual(rows['authority_activation'], [])

    def test_graceful_release_does_not_create_fence(self):
        self.test_real_child_activates_releases_and_exits_without_manual_pumping()
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'], [])

    def test_explicit_stop_preserves_existing_unfenced_semantics(self):
        proc, channel, active, before, activations = self.activated_with_pending_obligation()
        reply = self.control(channel, stop=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('view'), reply)
        self.assertEqual(proc.wait(timeout=5), 0)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.activation_rows(), activations)
        self.assertEqual(self.barrier().pending_count, 1)
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'], [])

    def test_changed_signing_key_preserves_cleanup_and_original_failure(self):
        proc, channel, active, before, activations = self.activated_with_pending_obligation()
        self.issuer.supervisor_key.write_bytes(self.issuer.signer.read_bytes())
        self.fail_child(proc, channel)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.activation_rows(), activations)
        self.assertEqual(self.barrier().pending_count, 1)
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'], [])

    def test_issuer_loss_cannot_prevent_local_cleanup_or_fabricate_fence(self):
        proc, channel = self.running()
        self.activate_child(proc)
        activations = self.activation_rows()
        self.issuer.stop_daemons()
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(proc.wait(timeout=8), 2)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.activation_rows(), activations)
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'], [])

    def test_native_cleanup_uncertainty_preserves_automatic_fence(self):
        proc, channel = self.running()
        self.activate_child(proc)
        before = self.read_record()
        result = self.supervisor.call(revoke=security.Revoke(credential_id=before.native.credential_id))
        self.assertEqual(result.code, security.OK)
        self.assertTrue(result.revoked)
        self.fail_child(proc, channel)
        record = self.read_record()
        self.assertEqual(record.stage, launch.CLEANUP_PENDING)
        self.assertTrue(record.HasField('fence'))
        self.assertTrue(record.HasField('process_stopped'))
        self.assertFalse(record.HasField('native_stopped'))
        self.assertEqual(len(self.issuer.snapshot_all()['authority_fence']), 1)

    def test_failed_journal_commit_retains_remote_fence_and_original_error(self):
        proc, channel = self.running()
        self.activate_child(proc)
        before = self.read_record()
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute("CREATE TRIGGER refuse_crash_update BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'crash journal fault'); END")
        self.fail_child(proc, channel)
        self.assertEqual(self.read_record(), before)
        self.assertEqual(len(self.issuer.snapshot_all()['authority_fence']), 1)


if __name__ == '__main__':
    suite = unittest.TestSuite(cls(name) for cls in (SupervisorCrashFencingTests, SupervisorCrashFailureTests)
        for name in cls.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
