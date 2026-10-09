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


if __name__ == '__main__':
    suite = unittest.TestSuite(SupervisorCrashFencingTests(name)
        for name in SupervisorCrashFencingTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
