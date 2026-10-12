"""Preassigned service identity reaches real issuer authentication and durable activation."""
import os
import select
import socket
import unittest
from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as pb
import test_identity_participant_runtime as existing

class IncarnationTests(existing.ParticipantRuntimeTests):
    def start_assigned(self, incarnation, domains=None, endpoint_port=None, **changes):
        config=pb.AuthorityParticipantRuntimeConfig(protocol_version=1,issuer_client_config=str(self.config_file(endpoint_port)),
            private_key_path=str(self.signer),participant_id=self.definition.id,registered_revision=1)
        for name,value in changes.items():setattr(config,name,value)
        path=self.root/('assigned-'+str(len(self.connections))+'.textproto')
        # Write the same text input against both the old and new protobuf descriptors.
        encoded=''.join('\\%03o'%value for value in incarnation)
        path.write_text(text_format.MessageToString(config)+'service_incarnation: "'+encoded+'"\n');path.chmod(0o600)
        return existing.RuntimeProcess(self,path,domains or ['world','world-one'])

    def ready_assigned(self, incarnation, **changes):
        runtime=self.start_assigned(incarnation,**changes);auth=runtime.receive()
        self.assertTrue(auth.HasField('authenticated'),auth)
        self.assertEqual(auth.authenticated.registration,self.registration)
        self.assertEqual(auth.authenticated.service_incarnation,incarnation)
        batch=runtime.receive();self.assertTrue(batch.HasField('revocations'),batch)
        return runtime,auth.authenticated,batch.revocations

    def test_exact_preassigned_incarnation_reaches_durable_activation(self):
        incarnation=os.urandom(32);runtime,auth,batch=self.ready_assigned(incarnation)
        state=self.activate(runtime,revision=batch.through_revision)
        self.assertEqual(state.service_incarnation,incarnation)
        self.assertEqual(self.rows(),[(1,incarnation,0,1)])
        self.assertEqual(auth.registration,self.registration)

    def test_explicit_invalid_incarnation_is_rejected_before_network_access(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(8);self.addCleanup(listener.close)
        for value in (b'',b'x'*31,b'x'*33):
            with self.subTest(length=len(value)):
                runtime=self.start_assigned(value,endpoint_port=listener.getsockname()[1])
                self.assertEqual(runtime.proc.wait(timeout=3),2)
                self.assertEqual(runtime.proc.stdout.read(),b'')
                self.assertIn('Invalid authority participant configuration',runtime.log.read_text())
                self.assertFalse(select.select([listener],[],[],0)[0],'Malformed incarnation reached the network')
                self.assertEqual(self.rows(),[])

    def test_preassigned_incarnation_cannot_be_reused_after_release(self):
        first=os.urandom(32);runtime,_,batch=self.ready_assigned(first);state=self.activate(runtime,batch.through_revision)
        runtime.send(2,release=pb.AuthorityRelease(generation=state.generation,applied_revision=0))
        self.assertTrue(runtime.receive().HasField('released'));self.assertEqual(runtime.proc.wait(timeout=2),0)
        reused,_,batch=self.ready_assigned(first)
        reused.send(3,activate=pb.AuthorityActivate(synchronized_revision=batch.through_revision))
        self.error(reused,core.ALREADY_EXISTS);self.assertEqual(self.rows(),[(1,first,0,0)])
        second=os.urandom(32);replacement,_,batch=self.ready_assigned(second);state=self.activate(replacement,batch.through_revision)
        self.assertEqual(state.generation,2);self.assertEqual(state.service_incarnation,second)
        self.assertEqual(self.rows(),[(1,first,0,0),(2,second,0,1)])

    def test_assignment_does_not_bypass_registered_domains(self):
        runtime=self.start_assigned(os.urandom(32),domains=['world','different'])
        self.error(runtime,core.INVALID_ARGUMENT);self.assertEqual(self.rows(),[])

    def test_assignment_does_not_bypass_service_key_proof(self):
        key,_=self.key('wrong-service-key')
        runtime=self.start_assigned(os.urandom(32),private_key_path=str(key))
        self.error(runtime,core.UNAUTHORIZED);self.assertEqual(self.rows(),[])

if __name__=='__main__':
    suite=unittest.TestSuite(IncarnationTests(n) for n in IncarnationTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
