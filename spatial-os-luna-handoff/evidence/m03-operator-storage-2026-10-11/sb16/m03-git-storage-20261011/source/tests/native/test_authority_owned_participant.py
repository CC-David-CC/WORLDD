"""Owned process execution requires approval of the coordinator's actual participant connection."""
import os
import sqlite3
from pathlib import Path
import sys
import time
import unittest
from google.protobuf import text_format
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import test_authority_launch as existing
import test_supervisor_registry as registry
import issuer_pb2 as identity
import launch_pb2 as pb
import process_scope_pb2 as process_pb

class OwnedParticipantTests(existing.AuthorityLaunchTests):
    def issuer_fixture(self):
        return registry.SupervisorRegistryTests('runTest')

    def setUp(self):
        super().setUp()
        self.issuer=self.issuer_fixture()
        def cleanup_issuer():
            self.issuer.tearDown()
            self.assertTrue(self.issuer.doCleanups(),'Issuer fixture cleanup failed')
        self.addCleanup(cleanup_issuer);self.issuer.setUp()
        self.assertTrue(self.issuer.bind().HasField('supervision'))
        self.binding.CopyFrom(pb.AuthorityLaunchBinding(issuer_id=self.issuer.info.issuer_id,
            participant_id=self.issuer.registration.participant.id,registered_revision=self.issuer.registration.registered_revision,
            service_incarnation=os.urandom(32),supervised=True))

    def runtime_config(self,**changes):
        config=identity.AuthorityParticipantRuntimeConfig(protocol_version=1,
            issuer_client_config=str(self.issuer.config_file()),private_key_path=str(self.issuer.signer),
            participant_id=self.binding.participant_id,registered_revision=self.binding.registered_revision,
            service_incarnation=self.binding.service_incarnation)
        for n,v in changes.items():setattr(config,n,v)
        path=self.root/('runtime-'+str(time.monotonic_ns())+'.textproto')
        path.write_text(text_format.MessageToString(config));path.chmod(0o600);return path

    def attach(self,proc,**changes):
        return self.call(proc,attach=pb.AuthorityParticipantAttach(runtime_config=str(self.runtime_config(**changes)),
            domains=self.issuer.registration.participant.domains))

    def update(self,proc,field):
        deadline=time.monotonic()+5
        while True:
            reply=self.call(proc,participant_poll=pb.AuthorityLaunchQuery())
            self.assertFalse(reply.HasField('error'),reply)
            if reply.HasField('participant_update'):
                self.assertTrue(reply.participant_update.HasField(field),reply);return reply.participant_update
            self.assertLess(time.monotonic(),deadline,'Owned participant update deadline');time.sleep(.005)

    def test_supervised_child_cannot_execute_without_durable_authorization(self):
        proc=self.probe();view=self.prepared(proc);observed=self.root/'unauthorized-execution'
        reply=self.run_child(proc,f'import pathlib;pathlib.Path({str(observed)!r}).touch()')
        self.assertTrue(reply.HasField('error'),reply)
        self.assertFalse(observed.exists());self.assertEqual(self.read_record(),view.record)
        self.assertEqual((self.cgroup/view.record.process.scope_name/'cgroup.procs').read_text(),'')
        self.stopped(proc)

    def test_coordinator_owns_real_authenticated_participant_connection(self):
        proc=self.probe();self.prepared(proc)
        reply=self.attach(proc);self.assertTrue(reply.HasField('view'),reply)
        auth=self.update(proc,'authenticated').authenticated
        self.assertEqual(auth.registration,self.issuer.registration)
        self.assertEqual(auth.service_incarnation,self.binding.service_incarnation)
        self.assertEqual(auth.issuer_id,self.binding.issuer_id)
        self.assertEqual(len(auth.connection_binding),32)
        batch=self.update(proc,'revocations').revocations;self.assertTrue(batch.initial)
        self.stopped(proc)

    def connected(self):
        proc=self.probe();view=self.prepared(proc)
        self.assertTrue(self.attach(proc).HasField('view'))
        auth=self.update(proc,'authenticated').authenticated
        batch=self.update(proc,'revocations').revocations
        return proc,view,auth,batch

    def authorize(self,proc,**changes):
        request=pb.AuthorityLaunchAuthorize(issuer_client_config=str(self.issuer.config_file()),
            private_key_path=str(self.issuer.supervisor_key),supervision_revision=1,previous_generation=0)
        for n,v in changes.items():setattr(request,n,v)
        return self.call(proc,authorize=request)

    def activation_rows(self):
        return self.issuer.snapshot_all()['authority_activation']

    def activate(self,proc,revision=0):
        return self.call(proc,participant_command=identity.ParticipantCommand(request_id=71,
            activate=identity.AuthorityActivate(synchronized_revision=revision)))

    def test_child_observes_durable_exact_approval_and_retained_connection_activates(self):
        proc,view,auth,batch=self.connected();approved=self.authorize(proc)
        self.assertTrue(approved.HasField('view'),approved);record=approved.view.record
        self.assertEqual(record,self.read_record());self.assertEqual(record.schema_version,2)
        self.assertEqual(record.authorization.request.service,auth)
        self.assertEqual(record.authorization.request.launch_id,view.record.process.launch_id)
        self.assertEqual(record.authorization.supervisor_public_key,self.issuer.supervisor_public)
        self.assertEqual(record.authorization.generation,0)
        for private in [str(self.issuer.supervisor_key).encode(),self.issuer.supervisor_key.read_bytes()]:
            self.assertNotIn(private,record.SerializeToString())
        observed=self.root/'approved-execution'
        code=('import pathlib,sqlite3,time\n'
            f'db=sqlite3.connect("file:"+{str(self.journal/"launch.db")!r}+"?mode=ro",uri=True)\n'
            'data=db.execute("SELECT record FROM launch WHERE singleton=1").fetchone()[0]\n'
            f'p=pathlib.Path({str(observed)!r});t=p.with_suffix(".tmp");t.write_bytes(data);t.replace(p);time.sleep(30)')
        started=self.run_child(proc,code);self.assertTrue(started.HasField('view'),started);self.wait_file(observed)
        actual=pb.AuthorityLaunchRecord.FromString(observed.read_bytes());self.assertEqual(actual,started.view.record)
        self.assertEqual(actual.authorization,record.authorization)
        self.assertTrue(self.activate(proc,batch.through_revision).HasField('view'))
        active=self.update(proc,'activated').activated
        self.assertEqual(active.generation,1);self.assertEqual(active.service_incarnation,self.binding.service_incarnation)
        self.assertEqual(len(self.activation_rows()),1)
        reply=self.call(proc,participant_command=identity.ParticipantCommand(request_id=72,
            release=identity.AuthorityRelease(generation=1,applied_revision=batch.through_revision)))
        self.assertTrue(reply.HasField('view'));self.assertTrue(self.update(proc,'released').HasField('released'))
        self.stopped(proc)

    def test_identity_mismatch_is_rejected_before_attaching_a_worker(self):
        proc=self.probe();view=self.prepared(proc)
        for changes in [dict(participant_id=b'x'*32),dict(registered_revision=2),dict(service_incarnation=b'x'*32)]:
            reply=self.attach(proc,**changes);self.assertTrue(reply.HasField('error'),reply)
            self.assertIn('differs from owned launch',reply.error.message)
            self.assertEqual(self.read_record(),view.record)
        self.assertTrue(self.attach(proc).HasField('view'))
        self.update(proc,'authenticated');self.update(proc,'revocations');self.stopped(proc)

    def test_wrong_supervisor_keys_cannot_approve_or_start_the_owned_launch(self):
        proc,view,_,_=self.connected()
        for key in [self.issuer.signer,self.issuer.owner]:
            self.assertTrue(self.authorize(proc,private_key_path=str(key)).HasField('error'))
            self.assertEqual(self.read_record(),view.record)
            self.assertTrue(self.run_child(proc,'raise SystemExit(0)').HasField('error'))
        self.assertEqual(self.issuer.snapshot_all()['authority_launch_authorization'],[])
        self.stopped(proc)

    def test_remote_approval_without_journal_commit_cannot_execute_and_exact_retry_recovers(self):
        proc,view,_,_=self.connected()
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute("CREATE TRIGGER deny_approval BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'approval fault'); END")
        reply=self.authorize(proc);self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(self.read_record(),view.record)
        rows=self.issuer.snapshot_all()['authority_launch_authorization'];self.assertEqual(len(rows),1)
        self.assertTrue(self.run_child(proc,'raise SystemExit(0)').HasField('error'))
        with sqlite3.connect(self.journal/'launch.db') as db:db.execute('DROP TRIGGER deny_approval')
        reply=self.authorize(proc);self.assertTrue(reply.HasField('view'),reply)
        self.assertEqual(self.issuer.snapshot_all()['authority_launch_authorization'],rows)
        self.stopped(proc)

    def test_activation_cannot_precede_the_owned_child_start(self):
        proc,_,_,_=self.connected();self.assertTrue(self.authorize(proc).HasField('view'))
        self.assertTrue(self.activate(proc).HasField('error'));self.assertEqual(self.activation_rows(),[])
        self.stopped(proc)

    def test_failed_exec_cannot_activate_a_generation(self):
        proc,_,_,_=self.connected();self.assertTrue(self.authorize(proc).HasField('view'))
        reply=self.call(proc,start=process_pb.AuthorityProcessLaunch(executable='/nonexistent-spatial-authority'))
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(self.read_record().stage,pb.START_ATTEMPTED)
        self.assertTrue(self.activate(proc).HasField('error'));self.assertEqual(self.activation_rows(),[])
        self.stopped(proc)

    def test_worker_failure_blocks_execution_after_approval(self):
        proc,_,_,_=self.connected();self.assertTrue(self.authorize(proc).HasField('view'))
        self.issuer.stop_daemons();self.update(proc,'error')
        self.assertTrue(self.run_child(proc,'raise SystemExit(0)').HasField('error'))
        self.assertEqual(self.activation_rows(),[]);self.stopped(proc)

    def test_restart_does_not_reconstruct_approved_live_ownership(self):
        proc,_,_,_=self.connected();reply=self.authorize(proc);self.assertTrue(reply.HasField('view'),reply)
        record=reply.view.record;proc.stdin.close();self.assertEqual(proc.wait(timeout=5),0)
        other=self.probe(start_server=False)
        self.assertEqual(self.call(other,inspect=pb.AuthorityLaunchQuery()).view.record,record)
        self.assertTrue(self.run_child(other,'raise SystemExit(0)').HasField('error'))
        self.assertTrue(self.attach(other).HasField('error'))
        self.assertTrue(self.activate(other).HasField('error'));self.assertEqual(self.activation_rows(),[])

    def test_cleanup_does_not_retire_an_activated_issuer_generation(self):
        proc,_,_,batch=self.connected();self.assertTrue(self.authorize(proc).HasField('view'))
        self.assertTrue(self.run_child(proc,'import time;time.sleep(30)').HasField('view'))
        self.assertTrue(self.activate(proc,batch.through_revision).HasField('view'));self.update(proc,'activated')
        before=self.activation_rows();self.stopped(proc);self.assertEqual(self.activation_rows(),before)
        self.assertTrue(self.activate(proc).HasField('error'))

if __name__=='__main__':
    suite=unittest.TestSuite(OwnedParticipantTests(n) for n in OwnedParticipantTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
