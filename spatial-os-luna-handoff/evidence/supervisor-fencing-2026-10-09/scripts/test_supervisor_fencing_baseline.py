"""The real supervisor must connect its owned launch to issuer fencing."""
import struct
from pathlib import Path
import sys
import unittest
SOURCE = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0,str(SOURCE/'tests/native'))
import test_authority_supervisor as supervisor
import launch_pb2 as launch


class SupervisorFencingBaselineTests(supervisor.AuthoritySupervisorTests):
    def test_supervisor_fences_its_actual_activated_child_before_checked_cleanup(self):
        proc, channel = self.running()
        active = self.activate_child(proc)
        before = self.read_record()
        # Additive protobuf field 20 is an empty AuthorityLaunchQuery.
        # This encodes the same request against old and new generated bindings.
        request = launch.AuthorityLaunchRequest(protocol_version=1,request_id=77).SerializeToString()+b'\xa2\x01\x00'
        channel.sendall(struct.pack('!I',len(request))+request)
        reply = self.message(channel,launch.AuthorityLaunchReply)
        self.assertEqual(reply.request_id,77)
        self.assertFalse(reply.HasField('error'),'Missing production supervisor fencing: '+str(reply))
        self.assertTrue(reply.HasField('view'),reply)
        record = reply.view.record
        self.assertEqual(record.stage,launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5),0)
        self.assertEqual(self.checked_cleanup(),record)
        self.assertTrue(record.HasField('fence'))
        self.assertEqual(record.fence.generation,active.generation)
        self.assertEqual(record.fence.launch_id,before.process.launch_id)
        self.assertEqual(record.fence.authorization_id,before.authorization.authorization_id)
        self.assertEqual(record.fence.registration,self.issuer.registration)
        self.assertEqual(record.fence.service_incarnation,before.binding.service_incarnation)
        self.assertEqual(record.fence.supervisor_public_key,self.issuer.supervisor_public)
        self.assertEqual(record.authorization,before.authorization)
        rows = self.issuer.snapshot_all()
        self.assertEqual(len(rows['authority_fence']),1)
        self.assertEqual(len(rows['authority_activation']),1)
        self.assertEqual(rows['authority_activation'][0][-1],1)


if __name__ == '__main__':
    suite = unittest.TestSuite(SupervisorFencingBaselineTests(name)
        for name in SupervisorFencingBaselineTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
