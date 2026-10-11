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


import sqlite3
import issuer_pb2 as identity
import security_pb2 as security


class SupervisorFenceFailures(supervisor.AuthoritySupervisorTests):
    def failed_fence(self,channel):
        request=launch.AuthorityLaunchRequest(protocol_version=1,request_id=77,fence=launch.AuthorityLaunchQuery()).SerializeToString()
        channel.sendall(struct.pack('!I',len(request))+request)
        reply=self.message(channel,launch.AuthorityLaunchReply)
        self.assertEqual(reply.request_id,0) # Existing fatal supervisor error channel.
        self.assertTrue(reply.HasField('error'),reply)
        return reply

    def test_pending_revocation_survives_fenced_cleanup(self):
        proc,channel=self.running();self.activate_child(proc)
        self.revoke();self.child_update(proc,'revocations')
        self.assertEqual(self.barrier().pending_count,1)
        reply=self.control(channel,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('view'),reply)
        self.assertEqual(proc.wait(timeout=5),0)
        record=self.checked_cleanup();self.assertTrue(record.HasField('fence'))
        self.assertEqual(record.fence.security_revision,1)
        pending=self.barrier()
        self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False))

    def test_unconsumed_authorization_cannot_be_fenced(self):
        proc,channel=self.running()
        reply=self.failed_fence(channel)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(proc.wait(timeout=5),2)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        rows=self.issuer.snapshot_all()
        self.assertEqual(rows['authority_fence'],[]);self.assertEqual(rows['authority_activation'],[])

    def test_replaced_signing_key_cannot_fence_owned_launch(self):
        proc,channel=self.running();self.activate_child(proc)
        self.issuer.supervisor_key.write_bytes(self.issuer.signer.read_bytes())
        reply=self.failed_fence(channel)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertIn('Fence signing key differs',reply.error.message)
        self.assertEqual(proc.wait(timeout=5),2)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'],[])

    def test_native_cleanup_uncertainty_preserves_committed_fence(self):
        proc,channel=self.running();self.activate_child(proc)
        before=self.read_record()
        revoked=self.supervisor.call(revoke=security.Revoke(credential_id=before.native.credential_id))
        self.assertEqual(revoked.code,security.OK);self.assertTrue(revoked.revoked)
        reply=self.failed_fence(channel)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(proc.wait(timeout=5),2)
        record=self.read_record()
        self.assertEqual(record.stage,launch.CLEANUP_PENDING)
        self.assertTrue(record.HasField('fence'));self.assertTrue(record.HasField('process_stopped'))
        self.assertFalse(record.HasField('native_stopped'))
        self.assertEqual(len(self.issuer.snapshot_all()['authority_fence']),1)

    def test_failed_local_commit_never_reports_success(self):
        proc,channel=self.running();self.activate_child(proc)
        before=self.read_record()
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute("CREATE TRIGGER refuse_fence BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'fence journal fault'); END")
        reply=self.failed_fence(channel)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(proc.wait(timeout=5),2)
        self.assertEqual(self.read_record(),before)
        self.assertEqual(len(self.issuer.snapshot_all()['authority_fence']),1)


class CoordinatorFenceTests(supervisor.owned.OwnedParticipantTests):
    def live_authorized(self):
        proc,view,auth,batch=self.connected()
        self.assertTrue(self.authorize(proc).HasField('view'))
        self.assertTrue(self.run_child(proc,'import time;time.sleep(30)').HasField('view'))
        self.assertTrue(self.activate(proc,batch.through_revision).HasField('view'))
        self.update(proc,'activated')
        return proc

    def test_matching_fence_retry_preserves_record_and_rejects_further_participant_commands(self):
        proc=self.live_authorized()
        reply=self.call(proc,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('view'),reply);record=reply.view.record
        self.assertTrue(record.HasField('fence'))
        self.assertEqual(self.call(proc,fence=launch.AuthorityLaunchQuery()).view.record,record)
        self.assertTrue(self.activate(proc).HasField('error'))
        stopped=self.stopped(proc)
        self.assertEqual(stopped.fence,record.fence)
        self.assertEqual(self.call(proc,fence=launch.AuthorityLaunchQuery()).view.record,stopped)

    def test_lost_local_commit_can_retry_exact_remote_fence_with_retained_ownership(self):
        proc=self.live_authorized();before=self.read_record()
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute("CREATE TRIGGER refuse_fence BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'fence journal fault'); END")
        reply=self.call(proc,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(self.read_record(),before)
        rows=self.issuer.snapshot_all()['authority_fence'];self.assertEqual(len(rows),1)
        with sqlite3.connect(self.journal/'launch.db') as db:db.execute('DROP TRIGGER refuse_fence')
        retry=self.call(proc,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(retry.HasField('view'),retry)
        self.assertTrue(retry.view.record.HasField('fence'))
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'],rows)
        self.stopped(proc)

    def test_released_generation_cannot_be_fenced(self):
        proc=self.live_authorized()
        reply=self.call(proc,participant_command=identity.ParticipantCommand(request_id=72,
            release=identity.AuthorityRelease(generation=1,applied_revision=0)))
        self.assertTrue(reply.HasField('view'),reply);self.update(proc,'released')
        reply=self.call(proc,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('error'),reply)
        self.assertFalse(self.read_record().HasField('fence'))
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'],[])
        self.stopped(proc)

    def test_reopened_journal_rejects_mismatched_fence_and_cannot_reconstruct_ownership(self):
        proc=self.live_authorized()
        self.assertTrue(self.call(proc,fence=launch.AuthorityLaunchQuery()).HasField('view'))
        record=self.stopped(proc)
        proc.stdin.close();self.assertEqual(proc.wait(timeout=5),0)
        for field,value in [('issuer_id',b'x'*32),('generation',2),('service_incarnation',b'x'*32),
                ('authorization_id',b'x'*32),('launch_id',b'x'*32),('supervision_revision',2),
                ('supervisor_public_key',b'x'*32),('fence_id',b''),('security_revision',2**63)]:
            changed=launch.AuthorityLaunchRecord();changed.CopyFrom(record)
            setattr(changed.fence,field,value)
            with sqlite3.connect(self.journal/'launch.db') as db:
                db.execute('UPDATE launch SET record=?',(changed.SerializeToString(),))
            rejected=self.probe(start_server=False);self.assertEqual(rejected.wait(timeout=5),2)
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute('UPDATE launch SET record=?',(record.SerializeToString(),))
        reopened=self.probe(start_server=False)
        self.assertEqual(self.call(reopened,inspect=launch.AuthorityLaunchQuery()).view.record,record)
        self.assertTrue(self.call(reopened,fence=launch.AuthorityLaunchQuery()).HasField('error'))
        self.assertTrue(self.call(reopened,prepare=self.binding).HasField('error'))


if __name__ == '__main__':
    suite=unittest.TestSuite(cls(name) for cls in
        (SupervisorFencingBaselineTests,SupervisorFenceFailures,CoordinatorFenceTests)
        for name in cls.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
