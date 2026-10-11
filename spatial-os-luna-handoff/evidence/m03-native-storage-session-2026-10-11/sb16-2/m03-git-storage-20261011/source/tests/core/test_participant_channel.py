"""An inherited participant channel reaches the real issuer without child signing material."""
import os
import array
import fcntl
from pathlib import Path
import socket
import struct
import subprocess
import time
import unittest
from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as pb
import test_identity_participant_runtime as existing
from harness.wire import frame

class PrivateRuntime(existing.RuntimeProcess):
    def __init__(self,fixture,config,fd,check_exec=False):
        self.fixture=fixture;self.log=fixture.root/('private-participant-'+str(len(fixture.connections))+'.log')
        self.stderr=self.log.open('wb')
        self.proc=subprocess.Popen([str(existing.PROBE),*(['--check-channel-exec',str(fd)] if check_exec else []),str(config),'world','world-one'],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=self.stderr,pass_fds=(fd,))
        fixture.connections.append(self)

class ParticipantChannelTests(existing.ParticipantRuntimeTests):
    def private(self,check_exec=False,**changes):
        parent,child=socket.socketpair();parent.settimeout(3);self.addCleanup(parent.close)
        if check_exec:
            moved=socket.socket(fileno=fcntl.fcntl(child.fileno(),fcntl.F_DUPFD_CLOEXEC,128));child.close();child=moved
        incarnation=os.urandom(32)
        config=pb.AuthorityParticipantRuntimeConfig(protocol_version=1,participant_id=self.definition.id,
            registered_revision=1,service_incarnation=incarnation,supervisor_channel_fd=child.fileno(),
            supervisor_issuer_id=self.info.issuer_id)
        for n,v in changes.items():setattr(config,n,v)
        path=self.root/('private-config-'+str(time.monotonic_ns())+'.textproto')
        path.write_text(text_format.MessageToString(config));path.chmod(0o600)
        try:runtime=PrivateRuntime(self,path,child.fileno(),check_exec)
        finally:child.close()
        return runtime,parent,incarnation,config

    def service(self,incarnation):
        peer=self.connect()
        reply=peer.call(authority_authentication_begin=pb.AuthorityAuthenticationBegin(participant_id=self.definition.id,
            registered_revision=1,service_incarnation=incarnation))
        self.assertTrue(reply.HasField('challenge'),reply)
        challenge=reply.challenge
        reply=peer.call(authority_authentication_complete=pb.AuthorityAuthenticationComplete(nonce=challenge.nonce,
            proof=self.proof(challenge,self.signer,self.public)))
        self.assertTrue(reply.HasField('authority_authentication'),reply)
        return peer,reply.authority_authentication

    def send_update(self,parent,request_id=0,**operation):
        parent.sendall(frame(pb.ParticipantUpdate(protocol_version=1,request_id=request_id,**operation)))

    def receive_command(self,parent):
        def exact(size):
            data=b''
            while len(data)<size:
                part=parent.recv(size-len(data));self.assertTrue(part,'Private child closed before command');data+=part
            return data
        size=struct.unpack('!I',exact(4))[0];self.assertLessEqual(size,1048576)
        return pb.ParticipantCommand.FromString(exact(size))

    def ready_private(self,**changes):
        runtime,parent,incarnation,config=self.private(**changes);peer,auth=self.service(incarnation)
        self.send_update(parent,authenticated=auth)
        received=runtime.receive();self.assertTrue(received.HasField('authenticated'),received)
        self.assertEqual(received.authenticated,auth)
        result=peer.call(revocations=pb.RevocationRead());self.assertTrue(result.HasField('revocations'),result)
        self.send_update(parent,revocations=result.revocations)
        received=runtime.receive();self.assertEqual(received.revocations,result.revocations)
        return runtime,parent,peer,auth,result.revocations,config

    def forward(self,parent,peer):
        command=self.receive_command(parent);operation=command.WhichOneof('operation')
        names={'activate':('authority_activate','activated'),'applied':('authority_applied','applied'),'release':('authority_release','released')}
        request,outcome=names[operation];reply=peer.call(**{request:getattr(command,operation)})
        if reply.HasField('error'):self.send_update(parent,command.request_id,error=reply.error)
        else:
            self.assertTrue(reply.HasField('authority_activation'),reply)
            self.send_update(parent,command.request_id,**{outcome:reply.authority_activation})
        return command,reply

    def test_inherited_channel_activates_and_releases_real_issuer_generation_without_child_key(self):
        runtime,parent,peer,auth,batch,config=self.ready_private()
        self.assertEqual(config.private_key_path,'');self.assertEqual(config.issuer_client_config,'')
        runtime.send(71,activate=pb.AuthorityActivate(synchronized_revision=batch.through_revision))
        command,reply=self.forward(parent,peer);self.assertEqual(command.request_id,71)
        update=runtime.receive();self.assertTrue(update.HasField('activated'),update)
        self.assertEqual(update.request_id,71);self.assertEqual(update.activated.service_incarnation,auth.service_incarnation)
        self.assertEqual(self.rows(),[(1,auth.service_incarnation,batch.through_revision,1)])
        runtime.send(72,release=pb.AuthorityRelease(generation=1,applied_revision=batch.through_revision))
        self.forward(parent,peer);update=runtime.receive();self.assertTrue(update.HasField('released'),update)
        self.assertEqual(update.request_id,72);self.assertEqual(runtime.proc.wait(timeout=3),0)
        self.assertEqual(self.rows(),[(1,auth.service_incarnation,batch.through_revision,0)])

    def activate_private(self):
        runtime,parent,peer,auth,batch,config=self.ready_private()
        runtime.send(1,activate=pb.AuthorityActivate(synchronized_revision=batch.through_revision))
        self.forward(parent,peer);update=runtime.receive();self.assertTrue(update.HasField('activated'),update)
        return runtime,parent,peer,auth,update.activated,config

    def test_child_acknowledges_real_revocation_only_after_explicit_command(self):
        runtime,parent,peer,auth,state,_=self.activate_private()
        self.revoke();reply=peer.call(revocations=pb.RevocationRead(after_revision=0))
        self.send_update(parent,revocations=reply.revocations);update=runtime.receive()
        self.assertEqual(update.revocations.through_revision,1);self.assertEqual(self.barrier().pending_count,1)
        self.assertEqual(self.rows(),[(1,auth.service_incarnation,0,1)])
        runtime.send(2,applied=pb.AuthorityApplied(generation=1,from_revision=0,through_revision=1))
        self.forward(parent,peer);update=runtime.receive();self.assertEqual(update.applied.applied_revision,1)
        self.assertEqual(self.barrier().pending_count,0)
        self.assertEqual(self.rows(),[(1,auth.service_incarnation,1,1)])

    def test_activation_race_requires_child_resynchronization_and_explicit_retry(self):
        runtime,parent,peer,auth,batch,_=self.ready_private()
        runtime.send(8,activate=pb.AuthorityActivate(synchronized_revision=0));self.revoke()
        self.forward(parent,peer);reply=runtime.receive();self.assertEqual(reply.error.code,core.REVISION_CONFLICT)
        self.assertEqual(reply.request_id,8);self.assertIsNone(runtime.proc.poll());self.assertEqual(self.rows(),[])
        events=peer.call(revocations=pb.RevocationRead(after_revision=0));self.send_update(parent,revocations=events.revocations)
        self.assertEqual(runtime.receive().revocations.through_revision,1)
        parent.settimeout(.05)
        with self.assertRaises(socket.timeout):parent.recv(1)
        parent.settimeout(3)
        runtime.send(9,activate=pb.AuthorityActivate(synchronized_revision=1));self.forward(parent,peer)
        self.assertEqual(runtime.receive().activated.applied_revision,1)
        self.assertEqual(self.rows(),[(1,auth.service_incarnation,1,1)])

    def test_mixed_signing_configuration_is_rejected_before_channel_use(self):
        for changes in [dict(private_key_path=str(self.signer)),dict(issuer_client_config=str(self.config_file())),
                        dict(service_incarnation=b''),dict(supervisor_issuer_id=b'x'),dict(supervisor_channel_fd=2)]:
            with self.subTest(changes=sorted(changes)):
                runtime,parent,_,_=self.private(**changes)
                self.assertEqual(runtime.proc.wait(timeout=3),2);self.assertEqual(runtime.proc.stdout.read(),b'')
                self.assertEqual(parent.recv(1),b'');self.assertEqual(self.rows(),[])

    def test_regular_file_cannot_be_used_as_private_channel(self):
        path=self.root/'not-a-socket';path.write_text('private descriptor regression')
        fd=os.open(path,os.O_RDONLY)
        try:
            config=pb.AuthorityParticipantRuntimeConfig(protocol_version=1,participant_id=self.definition.id,
                registered_revision=1,service_incarnation=b'i'*32,supervisor_channel_fd=fd,supervisor_issuer_id=self.info.issuer_id)
            config_path=self.root/'file-channel.textproto';config_path.write_text(text_format.MessageToString(config));config_path.chmod(0o600)
            runtime=PrivateRuntime(self,config_path,fd)
        finally:os.close(fd)
        self.assertEqual(runtime.proc.wait(timeout=3),2);self.assertEqual(runtime.proc.stdout.read(),b'')
        self.assertIn('unexpected kernel peer',runtime.log.read_text());self.assertEqual(self.rows(),[])

    def test_channel_is_nonblocking_and_closed_by_later_exec(self):
        runtime,parent,peer,auth,_,config=self.ready_private(check_exec=True)
        info=Path(f'/proc/{runtime.proc.pid}/fdinfo/{config.supervisor_channel_fd}').read_text()
        flags=int(next(line.split()[1] for line in info.splitlines() if line.startswith('flags:')),8)
        self.assertTrue(flags & os.O_NONBLOCK);self.assertTrue(flags & os.O_CLOEXEC)
        self.assertIsNone(runtime.proc.poll());self.assertEqual(self.rows(),[])

    def test_socket_from_another_process_cannot_impersonate_the_direct_parent(self):
        parent,child=socket.socketpair()
        maker=os.fork()
        if maker==0:
            parent.close();first,second=socket.socketpair()
            child.sendmsg([b'F'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[first.fileno()]))])
            child.recv(1);os._exit(0)
        child.close();fd=None
        try:
            parent.settimeout(3);_,ancillary,_,_=parent.recvmsg(1,socket.CMSG_SPACE(array.array('i').itemsize))
            received=array.array('i');received.frombytes(ancillary[0][2][:received.itemsize]);fd=received[0]
            with socket.socket(fileno=os.dup(fd)) as witness:
                peer_pid,peer_uid,_=struct.unpack('3i',witness.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            self.assertEqual(peer_pid,maker);self.assertEqual(peer_uid,os.geteuid())
            config=pb.AuthorityParticipantRuntimeConfig(protocol_version=1,participant_id=self.definition.id,
                registered_revision=1,service_incarnation=b'i'*32,supervisor_channel_fd=fd,supervisor_issuer_id=self.info.issuer_id)
            path=self.root/'wrong-parent.textproto';path.write_text(text_format.MessageToString(config));path.chmod(0o600)
            runtime=PrivateRuntime(self,path,fd)
            self.assertEqual(runtime.proc.wait(timeout=3),2);self.assertEqual(runtime.proc.stdout.read(),b'')
            self.assertIn('unexpected kernel peer',runtime.log.read_text())
        finally:
            if fd is not None:os.close(fd)
            parent.close();self.assertEqual(os.waitpid(maker,0)[1],0)

    def test_authentication_identity_and_domains_cannot_be_substituted(self):
        for mode in ['issuer','participant','registration','domain','incarnation','connection','issuer-process']:
            with self.subTest(mode=mode):
                runtime,parent,incarnation,_=self.private();peer,auth=self.service(incarnation)
                if mode=='issuer':auth.issuer_id=b'x'*32
                elif mode=='participant':auth.registration.participant.id=b'x'*32
                elif mode=='registration':auth.registration.registered_revision=2
                elif mode=='domain':auth.registration.participant.domains[0].id='other'
                elif mode=='incarnation':auth.service_incarnation=b'x'*32
                elif mode=='connection':auth.connection_binding=b''
                else:auth.issuer_incarnation=b''
                self.send_update(parent,authenticated=auth);self.error(runtime,core.INVALID_ARGUMENT)
                self.assertEqual(self.rows(),[])
                peer.close();parent.close();runtime.close()

    def test_invalid_revocation_batches_never_reach_the_consumer(self):
        for mode in ['issuer','gap','repeat-baseline','target','future-from']:
            with self.subTest(mode=mode):
                runtime,parent,_,_,_,_=self.ready_private()
                batch=pb.RevocationBatch(issuer_id=self.info.issuer_id,from_revision=0,through_revision=1,head_revision=1,
                    events=[pb.RevocationEvent(revision=1,target=pb.IdentityReference(kind=pb.ACTOR_TARGET,id=b'a'*32))])
                if mode=='issuer':batch.issuer_id=b'x'*32
                elif mode=='gap':batch.events[0].revision=2
                elif mode=='repeat-baseline':batch.initial=True
                elif mode=='target':batch.events[0].target.id=b'x'
                else:batch.from_revision=1
                self.send_update(parent,revocations=batch);self.error(runtime,core.INVALID_ARGUMENT)
                self.assertEqual(self.rows(),[])

    def state_for(self,auth):
        return pb.AuthorityActivationState(issuer_id=auth.issuer_id,registration=auth.registration,generation=1,
            service_incarnation=auth.service_incarnation,issuer_incarnation=auth.issuer_incarnation,
            connection_binding=auth.connection_binding,applied_revision=0,head_revision=0,active=True)

    def test_unsolicited_activation_is_rejected(self):
        runtime,parent,_,auth,_,_=self.ready_private()
        self.send_update(parent,33,activated=self.state_for(auth));self.error(runtime,core.INVALID_ARGUMENT)
        self.assertEqual(self.rows(),[])

    def test_correlated_reply_cannot_change_operation_identity_or_revision(self):
        for mode in ['request-id','operation','connection','incarnation','registration','generation','active','revision']:
            with self.subTest(mode=mode):
                runtime,parent,peer,auth,_,_=self.ready_private();runtime.send(55,activate=pb.AuthorityActivate(synchronized_revision=0))
                command=self.receive_command(parent);self.assertEqual(command.request_id,55)
                state=self.state_for(auth);request_id=55;outcome='activated'
                if mode=='request-id':request_id=56
                elif mode=='operation':outcome='applied'
                elif mode=='connection':state.connection_binding=b'x'*32
                elif mode=='incarnation':state.service_incarnation=b'x'*32
                elif mode=='registration':state.registration.registered_revision=2
                elif mode=='generation':state.generation=0
                elif mode=='active':state.active=False
                else:state.applied_revision=1
                self.send_update(parent,request_id,**{outcome:state});self.error(runtime,core.INVALID_ARGUMENT)
                self.assertEqual(self.rows(),[])
                peer.close();parent.close();runtime.close()

    def test_replayed_activation_reply_is_rejected_without_releasing_generation(self):
        runtime,parent,_,auth,state,_=self.activate_private()
        self.send_update(parent,1,activated=state);self.error(runtime,core.INVALID_ARGUMENT)
        self.assertEqual(self.rows(),[(1,auth.service_incarnation,0,1)])

    def test_malformed_excessive_and_truncated_frames_fail_closed(self):
        for payload,code,shutdown in [(struct.pack('!I',1048577),core.UNAVAILABLE,False),
            (struct.pack('!I',9)+b'x',core.UNAVAILABLE,True),(struct.pack('!I',1)+b'\xff',core.INVALID_ARGUMENT,False)]:
            with self.subTest(payload=payload):
                runtime,parent,_,_=self.private();parent.sendall(payload)
                if shutdown:parent.shutdown(socket.SHUT_WR)
                self.error(runtime,code);self.assertEqual(self.rows(),[])

    def test_unknown_update_fields_are_rejected(self):
        runtime,parent,incarnation,_=self.private();_,auth=self.service(incarnation)
        value=pb.ParticipantUpdate(protocol_version=1,authenticated=auth).SerializeToString()+b'\x98\x06\x01'
        parent.sendall(struct.pack('!I',len(value))+value);self.error(runtime,core.INVALID_ARGUMENT)

    def test_disconnect_permanently_disables_participation(self):
        runtime,parent,_,_,_,_=self.ready_private();parent.close()
        self.error(runtime,core.UNAVAILABLE);self.assertEqual(self.rows(),[])

    def test_incomplete_frame_expires(self):
        runtime,parent,_,_=self.private();parent.sendall(b'\x00')
        reply=runtime.receive(timeout=7);self.assertEqual(reply.error.code,core.UNAVAILABLE)
        self.assertEqual(runtime.proc.wait(timeout=2),2);self.assertEqual(self.rows(),[])

    def test_unanswered_command_expires_without_replay(self):
        runtime,parent,_,_,_,_=self.ready_private();runtime.send(44,activate=pb.AuthorityActivate(synchronized_revision=0))
        self.assertEqual(self.receive_command(parent).request_id,44)
        reply=runtime.receive(timeout=7);self.assertEqual(reply.error.code,core.UNAVAILABLE)
        self.assertEqual(reply.request_id,44);self.assertEqual(runtime.proc.wait(timeout=2),2)
        self.assertEqual(parent.recv(1),b'');self.assertEqual(self.rows(),[])

if __name__=='__main__':
    suite=unittest.TestSuite(ParticipantChannelTests(n) for n in ParticipantChannelTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
