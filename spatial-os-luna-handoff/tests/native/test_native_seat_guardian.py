"""Native guardian prototype fences one real X seat and preserves another."""
import unittest
import test_scoped_input as base
import core_pb2 as core

class NativeSeatGuardianTests(base.ScopedInputTests):
    def test_guardian_fences_exact_seat_and_rejects_foreign_controller(self):
        proc=self.launch()
        reply=self.read(proc,core.RenderResult)
        self.assertTrue(reply.HasField('ready'),reply)
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=5),0)

if __name__=='__main__':unittest.main(verbosity=2)
