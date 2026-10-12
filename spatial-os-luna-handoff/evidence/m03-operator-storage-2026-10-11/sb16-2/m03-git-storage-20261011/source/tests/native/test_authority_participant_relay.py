"""The supervisor passes exact approved identity through its owned child channel."""
import unittest
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import time
from unittest.mock import patch
from google.protobuf import text_format
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as identity
import process_scope_pb2 as process_pb
from harness.wire import frame
import test_authority_owned_participant as owned
import launch_pb2 as pb

class ParticipantRelayTests(owned.OwnedParticipantTests):
    def test_channel_configuration_requires_owned_approval_and_contains_no_signing_material(self):
        proc,_,auth,_=self.connected()
        self.assertTrue(self.authorize(proc).HasField('view'))
        result=self.call(proc,prepare_channel=pb.AuthorityParticipantChannelPrepare(target_fd=37))
        self.assertTrue(result.HasField('participant_config'),result)
        config=result.participant_config
        self.assertEqual(config.protocol_version,1)
        self.assertEqual(config.participant_id,auth.registration.participant.id)
        self.assertEqual(config.registered_revision,auth.registration.registered_revision)
        self.assertEqual(config.service_incarnation,auth.service_incarnation)
        self.assertEqual(config.supervisor_issuer_id,auth.issuer_id)
        self.assertEqual(config.supervisor_channel_fd,37)
        self.assertEqual(config.private_key_path,'');self.assertEqual(config.issuer_client_config,'')
        self.assertEqual(self.activation_rows(),[]);self.stopped(proc)

    def probe(self, start_server=True):
        if not getattr(self,'child_streams',False):
            return super().probe(start_server)
        incoming,writer=os.pipe();reader,outgoing=os.pipe()
        self.child_input=os.fdopen(writer,'wb',buffering=0)
        self.child_output=os.fdopen(reader,'rb',buffering=0)
        self.addCleanup(self.child_input.close);self.addCleanup(self.child_output.close)
        original=subprocess.Popen
        def launch(args,*rest,**options):
            if str(args[0])==str(owned.existing.PROBE):
                args=[*args,str(incoming),str(outgoing)]
                options['pass_fds']=(*options.get('pass_fds',()),incoming,outgoing)
            return original(args,*rest,**options)
        try:
            with patch.object(subprocess,'Popen',side_effect=launch):
                return super().probe(start_server)
        finally:os.close(incoming);os.close(outgoing)

    def configured(self):
        self.child_streams=True
        proc,view,auth,batch=self.connected()
        approved=self.authorize(proc);self.assertTrue(approved.HasField('view'),approved)
        result=self.call(proc,prepare_channel=pb.AuthorityParticipantChannelPrepare(target_fd=37))
        self.assertTrue(result.HasField('participant_config'),result)
        config=result.participant_config;path=self.root/'child-participant.textproto'
        path.write_text(text_format.MessageToString(config));path.chmod(0o600)
        return proc,view,auth,batch,config,path

    def start_channel(self,raw=False):
        proc,view,auth,batch,config,path=self.configured()
        if raw:
            # Independent byte peer. It does not implement issuer or participant state.
            code=('import os,select,socket\ns=socket.socket(fileno=37)\n'
                  'while True:\n'
                  ' ready,_,_=select.select([0,s],[],[],3)\n'
                  ' for source in ready:\n'
                  '  data=os.read(0,65536) if source==0 else s.recv(65536)\n'
                  '  if not data:raise SystemExit(0)\n'
                  '  if source==0:s.sendall(data)\n'
                  '  else:os.write(1,data)\n')
            request=process_pb.AuthorityProcessLaunch(executable='/usr/bin/python3',arguments=['-c',code])
        else:
            domains=[]
            for domain in self.issuer.registration.participant.domains:
                domains+=['world' if domain.kind==grants.WORLD else 'workspace',domain.id]
            request=process_pb.AuthorityProcessLaunch(executable=str(owned.existing.PROBE.with_name('spatial-participant-runtime-probe')),
                arguments=[str(path),*domains])
        result=self.call(proc,start=request);self.assertTrue(result.HasField('view'),result)
        pids=(self.cgroup/view.record.process.scope_name/'cgroup.procs').read_text().split()
        self.assertEqual(len(pids),1);self.authority_pid=int(pids[0])
        status=Path(f'/proc/{self.authority_pid}/status').read_text()
        self.assertEqual(int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:'))),proc.pid)
        self.assertEqual(self.child_update(proc,'authenticated').authenticated,auth)
        self.assertEqual(self.child_update(proc,'revocations').revocations,batch)
        return proc,view,auth

    def tick(self,proc):
        reply=self.call(proc,pump_channel=pb.AuthorityLaunchQuery())
        self.assertFalse(reply.HasField('error'),reply);return reply

    def child_update(self,proc,field):
        deadline=time.monotonic()+5
        def exact(size):
            value=b''
            while len(value)<size:
                self.tick(proc);self.assertLess(time.monotonic(),deadline,'Child relay update deadline')
                if not select.select([self.child_output],[],[],.005)[0]:continue
                data=os.read(self.child_output.fileno(),size-len(value));self.assertTrue(data,'Child output closed')
                value+=data
            return value
        size=struct.unpack('!I',exact(4))[0];self.assertLessEqual(size,1048576)
        result=identity.ParticipantUpdate.FromString(exact(size));self.assertEqual(result.protocol_version,1)
        self.assertTrue(result.HasField(field),result);return result

    def command(self,request_id=1,**operation):
        self.child_input.write(frame(identity.ParticipantCommand(request_id=request_id,**operation)))

    def activate_child(self,proc,revision=0):
        self.command(1,activate=identity.AuthorityActivate(synchronized_revision=revision))
        result=self.child_update(proc,'activated')
        self.assertEqual(result.request_id,1);self.assertEqual(result.activated.applied_revision,revision)
        return result.activated

    def revoke(self):
        peer=self.issuer.peer
        record=self.issuer.decide(peer,self.issuer.enroll(peer)).enrollment
        claims=self.issuer.claims(record,self.issuer.info)
        operation=identity.IssuerOperation(identity_revoke=identity.IdentityRevoke(
            target=identity.IdentityReference(kind=identity.ACTOR_TARGET,id=claims.actor_id),
            expected_security_revision=0,reason='supervisor relay regression'))
        result=self.issuer.execute(peer,self.issuer.challenge(peer,operation))
        self.assertTrue(result.HasField('identity_status'),result)

    def barrier(self):
        peer=self.issuer.peer
        operation=identity.IssuerOperation(barrier_read=identity.AuthorityBarrierRead(revision=1))
        result=self.issuer.execute(peer,self.issuer.challenge(peer,operation))
        self.assertTrue(result.HasField('authority_barrier'),result);return result.authority_barrier

    def relay_error(self,proc):
        deadline=time.monotonic()+5
        while True:
            reply=self.call(proc,pump_channel=pb.AuthorityLaunchQuery())
            if reply.HasField('error'):return reply
            self.assertLess(time.monotonic(),deadline,'Relay did not fail closed');time.sleep(.005)

    def test_channel_requires_approval_and_a_valid_unique_target(self):
        proc,view,_,_=self.connected()
        self.assertTrue(self.call(proc,prepare_channel=pb.AuthorityParticipantChannelPrepare(target_fd=37)).HasField('error'))
        self.assertTrue(self.authorize(proc).HasField('view'))
        for target in [0,1,2,256]:
            self.assertTrue(self.call(proc,prepare_channel=pb.AuthorityParticipantChannelPrepare(target_fd=target)).HasField('error'))
        self.assertTrue(self.call(proc,prepare_channel=pb.AuthorityParticipantChannelPrepare(target_fd=37)).HasField('participant_config'))
        self.assertTrue(self.call(proc,prepare_channel=pb.AuthorityParticipantChannelPrepare(target_fd=38)).HasField('error'))
        self.assertTrue(self.call(proc,pump_channel=pb.AuthorityLaunchQuery()).HasField('error'))
        self.assertEqual(self.activation_rows(),[]);self.stopped(proc)

    def test_relay_excludes_external_polling_and_commands(self):
        proc,_,_,_,_,_=self.configured()
        self.assertTrue(self.call(proc,participant_poll=pb.AuthorityLaunchQuery()).HasField('error'))
        self.assertTrue(self.activate(proc).HasField('error'))
        self.assertEqual(self.activation_rows(),[]);self.stopped(proc)

    def test_real_owned_child_activates_and_releases_exact_approved_generation(self):
        proc,view,auth=self.start_channel()
        info=Path(f'/proc/{self.authority_pid}/fdinfo/37').read_text()
        flags=int(next(line.split()[1] for line in info.splitlines() if line.startswith('flags:')),8)
        self.assertTrue(flags & os.O_CLOEXEC);self.assertTrue(flags & os.O_NONBLOCK)
        state=self.activate_child(proc);self.assertEqual(state.service_incarnation,auth.service_incarnation)
        self.assertEqual(state.connection_binding,auth.connection_binding)
        record=self.read_record();self.assertEqual(record.authorization.request.service,auth)
        self.assertEqual(record.process,view.record.process);self.assertEqual(len(self.activation_rows()),1)
        self.command(2,release=identity.AuthorityRelease(generation=state.generation,applied_revision=0))
        result=self.child_update(proc,'released');self.assertEqual(result.request_id,2);self.assertFalse(result.released.active)
        self.tick(proc);self.stopped(proc)

    def test_parent_receipt_and_child_delivery_do_not_acknowledge_revocation(self):
        proc,_,_=self.start_channel();state=self.activate_child(proc);self.revoke()
        update=self.child_update(proc,'revocations');self.assertEqual(update.revocations.through_revision,1)
        self.assertEqual(self.barrier().pending_count,1)
        self.tick(proc);self.assertEqual(self.barrier().pending_count,1)
        self.command(2,applied=identity.AuthorityApplied(generation=state.generation,from_revision=0,through_revision=1))
        result=self.child_update(proc,'applied');self.assertEqual(result.applied.applied_revision,1)
        self.assertEqual(self.barrier().pending_count,0)
        self.command(3,release=identity.AuthorityRelease(generation=state.generation,applied_revision=1))
        self.child_update(proc,'released');self.stopped(proc)

    def test_stale_child_revision_reaches_issuer_unchanged_and_requires_explicit_retry(self):
        proc,_,_=self.start_channel(raw=True);self.revoke()
        self.assertEqual(self.child_update(proc,'revocations').revocations.through_revision,1)
        self.command(8,activate=identity.AuthorityActivate(synchronized_revision=0))
        result=self.child_update(proc,'error')
        self.assertEqual((result.request_id,result.error.code),(8,core.REVISION_CONFLICT))
        self.assertEqual(self.activation_rows(),[])
        self.tick(proc);self.assertEqual(self.activation_rows(),[])
        self.command(9,activate=identity.AuthorityActivate(synchronized_revision=1))
        result=self.child_update(proc,'activated')
        self.assertEqual(result.request_id,9);self.assertEqual(result.activated.applied_revision,1)
        self.command(10,release=identity.AuthorityRelease(generation=1,applied_revision=1))
        self.child_update(proc,'released');self.stopped(proc)

    def test_malformed_child_frame_permanently_disables_relay_without_activation(self):
        proc,_,_=self.start_channel(raw=True)
        self.child_input.write(struct.pack('!I',1048577))
        self.relay_error(proc)
        self.assertTrue(self.call(proc,pump_channel=pb.AuthorityLaunchQuery()).HasField('error'))
        self.assertTrue(self.activate(proc).HasField('error'))
        self.assertEqual(self.activation_rows(),[]);self.stopped(proc)

    def test_failed_exec_cannot_retain_or_reuse_prepared_channel(self):
        proc,_,_,_,_,_=self.configured()
        result=self.call(proc,start=process_pb.AuthorityProcessLaunch(executable='/missing-relay-authority'))
        self.assertTrue(result.HasField('error'))
        self.assertEqual(self.read_record().stage,pb.START_ATTEMPTED)
        self.assertTrue(self.call(proc,pump_channel=pb.AuthorityLaunchQuery()).HasField('error'))
        self.assertTrue(self.run_child(proc,'pass').HasField('error'))
        self.assertEqual(self.activation_rows(),[]);self.stopped(proc)

    def test_child_death_preserves_pending_generation_and_revocation(self):
        proc,_,_=self.start_channel();self.activate_child(proc);self.revoke()
        self.child_update(proc,'revocations');self.assertEqual(self.barrier().pending_count,1)
        before=self.activation_rows();os.kill(self.authority_pid,signal.SIGKILL);self.relay_error(proc)
        self.assertEqual(self.activation_rows(),before);self.assertEqual(self.barrier().pending_count,1)
        self.stopped(proc);self.assertEqual(self.activation_rows(),before);self.assertEqual(self.barrier().pending_count,1)

    def test_restart_cannot_recreate_private_endpoint(self):
        proc,_,_,_,_,_=self.configured();record=self.read_record()
        proc.stdin.close();self.assertEqual(proc.wait(timeout=5),0)
        self.child_streams=False;other=self.probe(start_server=False)
        self.assertEqual(self.call(other,inspect=pb.AuthorityLaunchQuery()).view.record,record)
        self.assertTrue(self.call(other,prepare_channel=pb.AuthorityParticipantChannelPrepare(target_fd=37)).HasField('error'))
        self.assertTrue(self.call(other,pump_channel=pb.AuthorityLaunchQuery()).HasField('error'))
        self.assertEqual(self.activation_rows(),[])

if __name__=='__main__':
    suite=unittest.TestSuite(ParticipantRelayTests(n) for n in ParticipantRelayTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
