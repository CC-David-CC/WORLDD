"""Fence an actual owned world launch without claiming issuer retirement."""
import unittest
import test_supervised_world_participation as participation
import core_pb2 as core
import issuer_pb2 as identity
import key_pb2 as keys


class SupervisedWorldFencingTests(participation.SupervisedWorldParticipationTests):
    def crashed_owned_world(self):
        self.test_world_crash_cleans_native_processes_but_retains_issuer_obligation()
        record = self.read_record()
        self.assertTrue(record.HasField('process_stopped'))
        self.assertTrue(record.HasField('native_stopped'))
        self.assertEqual(record.process_stopped.identity, record.process)
        self.assertEqual(record.native_stopped.identity, record.native)
        self.assertEqual(record.authorization.request.launch_id, record.process.launch_id)
        self.assertEqual(self.activation_state(), [(1,0,1)])
        request = identity.AuthorityFenceBegin(participant_id=record.binding.participant_id,
            registered_revision=record.binding.registered_revision, generation=1,
            service_incarnation=record.binding.service_incarnation, launch_id=record.process.launch_id,
            supervision_revision=record.authorization.request.supervision_revision,
            authorization_id=record.authorization.authorization_id)
        return record, request

    def complete_fence(self, peer, request, key, public):
        result = peer.call(authority_fence_begin=request)
        self.assertTrue(result.HasField('challenge'), result)
        self.assertEqual(result.challenge.signing_request.purpose, keys.AUTHORITY_FENCING)
        proof = self.issuer.proof(result.challenge, key, public)
        return peer.call(authority_fence_complete=identity.AuthorityFenceComplete(
            nonce=result.challenge.nonce, proof=proof))

    def test_exact_crashed_world_can_be_fenced_without_retiring_its_obligation(self):
        record, request = self.crashed_owned_world()
        before = self.issuer.snapshot_all()
        peer = self.issuer.connect(); self.addCleanup(peer.close)
        result = self.complete_fence(peer,request,self.issuer.supervisor_key,self.issuer.supervisor_public)
        self.assertTrue(result.HasField('authority_fence'), result)
        fence = result.authority_fence
        self.assertEqual(fence.registration,self.issuer.registration)
        self.assertEqual(fence.launch_id,record.process.launch_id)
        self.assertEqual(fence.authorization_id,record.authorization.authorization_id)
        self.assertEqual(fence.service_incarnation,record.binding.service_incarnation)
        self.assertEqual(fence.supervisor_public_key,self.issuer.supervisor_public)
        self.assertEqual((fence.generation,fence.security_revision),(1,1))
        self.assertEqual(len(fence.fence_id),32)
        after = self.issuer.snapshot_all()
        self.assertEqual(len(after['authority_fence']),1)
        self.assertEqual({n:v for n,v in before.items() if n!='authority_fence'},
                         {n:v for n,v in after.items() if n!='authority_fence'})
        self.assertEqual(self.activation_state(),[(1,0,1)])
        pending = self.issuer_barrier()
        self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False))
        retry = self.complete_fence(peer,request,self.issuer.supervisor_key,self.issuer.supervisor_public)
        self.assertEqual(retry.authority_fence,fence)
        self.assertEqual(self.read_record(),record)

    def test_service_key_and_wrong_owned_launch_cannot_fence_crashed_world(self):
        record,request = self.crashed_owned_world()
        before = self.issuer.snapshot_all()
        peer = self.issuer.connect(); self.addCleanup(peer.close)
        result = self.complete_fence(peer,request,self.issuer.signer,self.issuer.public)
        self.assertTrue(result.HasField('error'),result)
        self.assertEqual(result.error.code,core.UNAUTHORIZED)
        request.launch_id = b'x'*32
        result = peer.call(authority_fence_begin=request)
        self.assertTrue(result.HasField('error'),result)
        self.assertEqual(result.error.code,core.UNAUTHORIZED)
        self.assertEqual(self.issuer.snapshot_all(),before)
        self.assertEqual(self.read_record(),record)
        self.assertEqual(self.issuer_barrier().pending_count,1)


if __name__ == '__main__':
    suite = unittest.TestSuite(SupervisedWorldFencingTests(name)
        for name in SupervisedWorldFencingTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
