"""Independent launch-owner bindings from real authenticated actors and native input."""
import array
import os
import socket
import subprocess
import time
import unittest
from pathlib import Path
import world_input_witness_v2_pb2 as witness
import test_world_application_control as control
import test_world_identity as world_fixture


class WorldInputWitnessTests(control.WorldApplicationControlTests):
    def setUp(self):
        super().setUp()
        self.witness,self.witness_child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        self.witness.settimeout(4)
        self.witness_inodes={os.fstat(self.witness.fileno()).st_ino,os.fstat(self.witness_child.fileno()).st_ino}
        self.addCleanup(self.witness.close);self.addCleanup(self.witness_child.close)

    def scoped_world(self,*args,**kwargs):
        kwargs['input_witness_fd']=self.witness_child.fileno()
        try:return super().scoped_world(*args,**kwargs)
        finally:self.witness_child.close()

    def prepare(self,prime_peer=True):
        self.setup_world();self.app,self.surface=self.probe()
        self.observe_grant,self.control_grant=self.grant_control(self.app)
        if prime_peer:
            self.actor_aim(self.peer);self.actor_pointer(True,self.peer)
            self.peer_pointer=self.native_device(self.app)
            self.actor_pointer(False,self.peer)
            self.control_action(self.peer,unfocus=control.core.ActorStopRequest())
        self.actor_aim();self.actor_pointer(True)
        self.old_pointer=self.native_device(self.app)
        self.old_state=self.actor_state();self.peer_state=self.actor_state(self.peer)
        request=witness.WorldInputWitnessRequestV2(protocol_version=2,session_id=os.urandom(16),arm_id=os.urandom(16),
            old_actor_entity_id=self.old_state.id,peer_actor_entity_id=self.peer_state.id,
            expected_server_id=self.server_id,expected_presentation_generation=1,
            deadline_monotonic_ns=time.monotonic_ns()+3_000_000_000)
        request.expected_application.workspace_id=self.app.workspace
        request.expected_application.application_id=self.app.application
        self.binding_request=request
        return request

    def send(self,request,suffix=b''):
        self.witness.sendall(request.SerializeToString(deterministic=True)+suffix)

    def receive_binding(self):
        raw=self.witness.recv(4097)
        self.assertTrue(raw,'World input witness closed before its reply')
        self.assertLessEqual(len(raw),4096)
        reply=witness.WorldInputWitnessReplyV2.FromString(raw)
        self.assertEqual(reply.protocol_version,2)
        self.assertEqual(reply.session_id,self.binding_request.session_id)
        self.assertEqual(reply.arm_id,self.binding_request.arm_id)
        self.assertEqual(reply.worldd_pid,self.world_process.proc.pid)
        self.assertEqual(reply.world_id,self.world_info['world_id'])
        self.assertEqual(self.witness.recv(4097),b'','The private binding channel must close after one reply')
        self.assertEqual(self.world_process.management('system.hello')['implementation'],'production')
        return reply

    def assert_denied(self,request,code):
        self.send(request);reply=self.receive_binding()
        self.assertEqual(reply.error_code,code,reply)
        self.assertFalse(reply.actors,reply)
        self.assertEqual(self.native_device(self.app),self.old_pointer)
        return reply

    def test_real_principals_activation_handles_and_native_devices_join(self):
        request=self.prepare();self.send(request);reply=self.receive_binding()
        self.assertEqual(reply.error_code,0,reply)
        self.assertEqual(reply.native_server_id,self.server_id)
        self.assertEqual(len(reply.actors),2,reply)
        old,peer=reply.actors
        for proof,state,identity,pointer in [(old,self.old_state,self.second,self.old_pointer),
                                            (peer,self.peer_state,self.identity,self.peer_pointer)]:
            b=proof.binding
            self.assertEqual(proof.actor_entity_id,state.id)
            self.assertTrue(proof.actor_entity_id.startswith('identity:'))
            self.assertEqual((b.actor.issuer_id,b.actor.actor_id),(identity.issuer_id,identity.actor_id))
            self.assertGreater(b.world_actor_id,0);self.assertGreater(b.native_seat_id,0)
            self.assertEqual((b.session_id,b.arm_id),(request.session_id,request.arm_id))
            self.assertEqual(b.application,request.expected_application)
            self.assertEqual(b.presentation_generation,1)
            self.assertEqual(proof.pointer_device,pointer)
            self.assertGreater(proof.keyboard_device,0)
            self.assertNotEqual(proof.keyboard_device,pointer)
        self.assertNotEqual(old.binding.world_actor_id,peer.binding.world_actor_id)
        self.assertNotEqual(old.binding.native_seat_id,peer.binding.native_seat_id)
        self.assertEqual(len({old.pointer_device,old.keyboard_device,peer.pointer_device,peer.keyboard_device}),4)
        after=self.actor_state()
        self.assertEqual(list(after.held_buttons),list(self.old_state.held_buttons))
        self.assertEqual(after.focus,self.old_state.focus)
        # A successful metadata query cannot hand the application to the peer.
        self.assertEqual(self.native_device(self.app),self.old_pointer)
        self.assertGreater(reply.compositor_pid,1)
        forbidden={f'socket:[{n}]' for n in self.witness_inodes}
        targets=[]
        for p in Path(f'/proc/{reply.compositor_pid}/fd').iterdir():
            try:targets.append(p.readlink().as_posix())
            except FileNotFoundError:pass
        self.assertFalse(forbidden.intersection(targets),'Witness endpoint leaked into the compositor exec')

    def test_unprimed_actor_is_not_given_a_native_seat(self):
        request=self.prepare(prime_peer=False)
        self.assertFalse(self.peer_state.HasField('focus'))
        self.assert_denied(request,control.core.NOT_FOUND)
        self.assertFalse(self.actor_state(self.peer).HasField('focus'))

    def test_unsigned_management_actor_cannot_supply_an_authenticated_binding(self):
        request=self.prepare()
        self.world_process.management('actor.open',id='unsigned-fixture',grants=['observe','navigate','interact'])
        request.peer_actor_entity_id='unsigned-fixture'
        self.assert_denied(request,control.core.FORBIDDEN)

    def test_unknown_actor_rejects_without_releasing_controller(self):
        request=self.prepare();request.peer_actor_entity_id='missing-actor'
        self.assert_denied(request,control.core.NOT_FOUND)

    def test_closed_actor_rejects(self):
        request=self.prepare()
        self.assertTrue(self.peer.call(close=control.session.WorldSessionClose()).HasField('closed'))
        self.assert_denied(request,control.core.FORBIDDEN)

    def test_revoked_control_authority_rejects_binding(self):
        request=self.prepare()
        self.revoke_grant(self.peer,self.control_grant)
        self.send(request);reply=self.receive_binding()
        self.assertEqual(reply.error_code,control.core.FORBIDDEN,reply);self.assertFalse(reply.actors)

    def test_duplicate_actor_selectors_reject(self):
        request=self.prepare();request.peer_actor_entity_id=request.old_actor_entity_id
        self.assert_denied(request,control.core.INVALID_ARGUMENT)

    def test_wrong_presentation_generation_rejects(self):
        request=self.prepare();request.expected_presentation_generation+=1
        self.assert_denied(request,control.core.STALE_TARGET)

    def test_wrong_native_server_rejects(self):
        request=self.prepare();request.expected_server_id=os.urandom(32)
        self.assert_denied(request,control.core.FORBIDDEN)

    def test_unknown_application_rejects(self):
        request=self.prepare();request.expected_application.application_id=b'nonexistent'
        self.assert_denied(request,control.core.FORBIDDEN)

    def test_expired_request_rejects(self):
        request=self.prepare();request.deadline_monotonic_ns=time.monotonic_ns()-1
        self.assert_denied(request,control.core.INVALID_ARGUMENT)

    def test_unknown_fields_close_the_private_channel_only(self):
        request=self.prepare();self.send(request,b'\x50\x01')
        self.assertEqual(self.witness.recv(4097),b'')
        self.assertEqual(self.native_device(self.app),self.old_pointer)
        self.assertTrue(self.world_process.management('system.hello'))

    def test_oversized_packet_closes_the_private_channel_only(self):
        self.prepare();self.witness.sendall(b'x'*4097)
        self.assertEqual(self.witness.recv(4097),b'')
        self.assertEqual(self.native_device(self.app),self.old_pointer)

    def test_forked_sender_cannot_use_launch_parent_authority(self):
        request=self.prepare();pid=os.fork()
        if pid==0:
            try:self.send(request)
            except BaseException:os._exit(1)
            os._exit(0)
        _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),0)
        self.assertEqual(self.witness.recv(4097),b'')
        self.assertEqual(self.native_device(self.app),self.old_pointer)

    def test_unexpected_descriptors_are_closed_and_refused(self):
        request=self.prepare();fd=os.memfd_create('world-witness-unexpected-right',os.MFD_CLOEXEC)
        try:self.witness.sendmsg([request.SerializeToString(deterministic=True)],
            [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))])
        finally:os.close(fd)
        self.assertEqual(self.witness.recv(4097),b'')
        self.assertEqual(self.native_device(self.app),self.old_pointer)
        targets=[]
        for p in Path(f'/proc/{self.world_process.proc.pid}/fd').iterdir():
            try:targets.append(p.readlink().as_posix())
            except FileNotFoundError:pass
        self.assertFalse(any('world-witness-unexpected-right' in target for target in targets))

    def test_eof_retires_endpoint_without_stopping_world(self):
        self.prepare();self.witness.close()
        self.assertTrue(self.world_process.management('system.hello'))
        end=time.monotonic()+2
        forbidden={f'socket:[{n}]' for n in self.witness_inodes}
        while True:
            targets=set()
            for p in Path(f'/proc/{self.world_process.proc.pid}/fd').iterdir():
                try:targets.add(p.readlink().as_posix())
                except FileNotFoundError:pass
            if not targets.intersection(forbidden):break
            self.assertLess(time.monotonic(),end);time.sleep(.01)
        self.assertEqual(self.native_device(self.app),self.old_pointer)

    def test_binding_channel_cannot_be_rearmed_after_one_reply(self):
        request=self.prepare();self.send(request);reply=self.receive_binding()
        self.assertEqual(reply.error_code,0,reply)
        with self.assertRaises((BrokenPipeError,ConnectionResetError)):self.send(request)
        self.assertTrue(self.world_process.management('system.hello'))

    def reject_startup(self,fd,diagnostic,extra=()):
        p=subprocess.run([str(world_fixture.WORLD),'--state-dir',str(self.root/'refused-world'),
                          '--input-witness-fd',str(fd),*extra],pass_fds=(fd,),capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,2,p.stderr);self.assertIn(diagnostic,p.stderr)

    def test_regular_file_cannot_be_witness_endpoint(self):
        fd=os.memfd_create('world-witness-wrong-class',os.MFD_CLOEXEC)
        try:self.reject_startup(fd,'requires a parent-created Unix seqpacket endpoint')
        finally:os.close(fd)

    def test_stream_socket_cannot_be_witness_endpoint(self):
        parent,child=socket.socketpair()
        try:self.reject_startup(child.fileno(),'requires a parent-created Unix seqpacket endpoint')
        finally:parent.close();child.close()

    def test_duplicate_witness_argument_rejects(self):
        self.reject_startup(self.witness_child.fileno(),'Unknown or incomplete worldd argument',
                            ('--input-witness-fd',str(self.witness_child.fileno())))


if __name__=='__main__':
    suite=unittest.TestSuite(WorldInputWitnessTests(name)
        for name in WorldInputWitnessTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
