"""Real Renderer, independent X witnesses, and independently revocable world input."""
import fcntl
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
import unittest
import compositor_native_pb2 as native
import core_pb2 as core
import presentation_probe_pb2 as probe
import security_pb2 as sec
from harness.wire import frame
EXECUTABLE=Path(sys.argv.pop(1)).resolve()
import test_compositor_identity as fixture

class PresentationBindingTests(unittest.TestCase):
    setUp=fixture.CompositorIdentityTests.setUp
    start=fixture.CompositorIdentityTests.start
    connect=fixture.CompositorIdentityTests.connect
    scene=fixture.CompositorIdentityTests.scene

    def configuration(self, credential, sealed=True):
        config=native.NativeCompositorConfig(protocol_version=1,display_number=self.display,
            compositor_cookie=credential.cookie,server_id=self.supervisor.inspect().server_id,
            server_pid=self.server.pid,server_uid=os.getuid())
        fd=os.memfd_create('presentation-test',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        os.write(fd,config.SerializeToString())
        if sealed:fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        return fd

    def launch(self, wrong_server=False):
        self.scene()
        self.owner.operation(2,self.owner.pack('III',self.window,1<<11,15))
        self.other=self.connect(self.supervisor.issue(workspace=b'two',application=b'editor').cookie)
        self.other_window=self.other.create_window()
        self.assertIsNone(self.other.operation(8,self.other.pack('I',self.other_window)))
        self.grants=[self.supervisor.issue(sec.COMPOSITOR,b'',b'') for _ in range(3)]
        descriptors=[self.configuration(g) for g in self.grants]
        descriptors.append(self.configuration(self.grants[1],sealed=False))
        if wrong_server:
            saved=(self.supervisor,self.display,self.server)
            self.start()
            descriptors.append(self.configuration(self.supervisor.issue(sec.COMPOSITOR,b'',b'')))
            self.supervisor,self.display,self.server=saved
        self.bindings=[native.WorldPresentationBinding(protocol_version=1,world_id=name,
            service_incarnation=bytes([i+1])*32,authority_generation=1,presentation_generation=i+1)
            for i,name in enumerate(('world-a','world-b'))]
        state=self.root/'renderer';state.mkdir()
        self.stream,child=socket.socketpair();self.stream.settimeout(10);self.sequence=0
        path=self.root/'presentation.log';log=path.open('wb')
        try:self.proc=subprocess.Popen([str(EXECUTABLE),str(state),*map(str,descriptors)],stdin=child,stdout=child,stderr=log,
            pass_fds=descriptors,env=dict(os.environ,LIBGL_ALWAYS_SOFTWARE='1'))
        finally:
            child.close()
            for fd in descriptors:os.close(fd)
        def cleanup():
            self.stream.close()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3)
            log.close();diagnostic=path.read_text(errors='replace')
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-presentation.log')).write_text(diagnostic)
            self.assertEqual(self.proc.returncode,0,diagnostic)
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,diagnostic)
        self.addCleanup(cleanup)
        self.assertTrue(self.receive().result.HasField('ready'))
        self.identity=(self.proc.pid,Path(f'/proc/{self.proc.pid}/stat').read_text().split()[21])
        deadline=time.monotonic()+5
        while True:
            surfaces=self.call(snapshot=True).result.snapshot.surfaces
            found=[s for s in surfaces if s.xid==self.window and s.mapped]
            second=[s for s in surfaces if s.xid==self.other_window and s.mapped]
            if found and second:self.target=found[0].handle;self.other_target=second[0].handle;break
            self.assertLess(time.monotonic(),deadline);time.sleep(.01)

    def receive(self):
        def exact(n):
            data=b''
            while len(data)<n:
                part=self.stream.recv(n-len(data));self.assertTrue(part,'Renderer exited');data+=part
            return data
        n=struct.unpack('!I',exact(4))[0];self.assertLessEqual(n,1048576)
        return probe.PresentationProbeReply.FromString(exact(n))

    def call(self,binding=None,**operation):
        self.sequence+=1
        request=probe.PresentationProbeRequest(protocol_version=1,request_id=self.sequence,**operation)
        if binding is not None:request.binding.CopyFrom(binding)
        self.stream.sendall(frame(request));reply=self.receive()
        self.assertEqual(reply.result.protocol_version,1);self.assertEqual(reply.result.request_id,self.sequence)
        return reply

    def bind(self,index):
        reply=self.call(self.bindings[index],bind_descriptor=index)
        self.assertTrue(reply.result.HasField('ready'),reply)

    def pointer(self,binding,pressed=True,target=None):
        command=core.RendererInputCommand(actor_id='same-actor',target=target or self.target,pixel=core.Pixel(x=1,y=1),
            pointer=core.ActorPointerRequest(button=1,pressed=pressed))
        return self.call(binding,input=command)

    def draw(self):
        command=core.RenderCommand(protocol_version=1,width=64,height=64)
        command.scene.camera.position.SetInParent();command.scene.camera.orientation.w=1
        command.scene.config.CopyFrom(core.RenderConfig(vertical_fov=1.57079632679,near_clip=.01,far_clip=1000,
            background=core.Color(red=17,green=34,blue=51),barrier_color=core.Color(red=1,green=2,blue=3)))
        reply=self.call(draw=command).result;self.assertTrue(reply.HasField('frame'),reply)
        head,body=self.supervisor.operation(73,self.supervisor.pack('IhhHHI',reply.frame.xid,1,1,1,1,0xffffffff),2,True)
        self.assertEqual(int.from_bytes(body,'little')&0xffffff,0x112233)
        self.assertEqual((self.proc.pid,Path(f'/proc/{self.proc.pid}/stat').read_text().split()[21]),self.identity)
        return reply.frame

    def test_revoking_one_world_preserves_real_graphics_and_other_input(self):
        self.launch();self.bind(0);self.bind(1)
        self.assertTrue(self.pointer(self.bindings[0]).result.HasField('input'))
        before=self.draw()
        self.assertEqual(self.supervisor.call(revoke=sec.Revoke(credential_id=self.grants[1].id)).code,sec.OK)
        self.assertEqual(self.pointer(self.bindings[0]).result.error.code,core.UNAVAILABLE)
        self.assertTrue(self.pointer(self.bindings[1]).result.HasField('input'))
        self.assertGreater(self.draw().frame_id,before.frame_id)

    def test_private_seat_query_reads_actual_same_named_actor_in_each_binding(self):
        self.launch();self.bind(0);self.bind(1)
        self.pointer(self.bindings[0]);self.pointer(self.bindings[1],target=self.other_target)
        first=self.call(self.bindings[0],seat_actor='same-actor')
        second=self.call(self.bindings[1],seat_actor='same-actor')
        for reply,binding in [(first,self.bindings[0]),(second,self.bindings[1])]:
            self.assertTrue(reply.HasField('seat'),reply)
            self.assertEqual(reply.seat.actor_entity_id,'same-actor')
            self.assertEqual(reply.seat.presentation_generation,binding.presentation_generation)
            self.assertEqual(reply.seat.server_id,self.supervisor.inspect().server_id)
            self.assertGreater(reply.seat.native_seat_id,0)
            self.assertGreater(reply.seat.pointer_device,0)
            self.assertNotEqual(reply.seat.pointer_device,reply.seat.keyboard_device)
        self.assertNotEqual(first.seat.native_seat_id,second.seat.native_seat_id)
        self.assertNotEqual(first.seat.pointer_device,second.seat.pointer_device)
        self.assertEqual(list(self.call(self.bindings[0],snapshot=True).result.snapshot.actors[0].held_buttons),[1])
        self.assertEqual(list(self.call(self.bindings[1],snapshot=True).result.snapshot.actors[0].held_buttons),[1])

    def test_private_seat_query_does_not_create_an_unprimed_actor(self):
        self.launch();self.bind(0)
        before=self.call(self.bindings[0],snapshot=True).result.snapshot
        reply=self.call(self.bindings[0],seat_actor='unprimed')
        self.assertEqual(reply.result.error.code,core.NOT_FOUND,reply)
        self.assertFalse(reply.HasField('seat'))
        self.assertEqual(len(self.call(self.bindings[0],snapshot=True).result.snapshot.actors),len(before.actors))

    def test_private_seat_query_refuses_retired_binding(self):
        self.launch();self.bind(0);self.pointer(self.bindings[0])
        self.call(self.bindings[0],retire=True)
        reply=self.call(self.bindings[0],seat_actor='same-actor')
        self.assertEqual(reply.result.error.code,core.STALE_TARGET,reply)
        self.assertFalse(reply.HasField('seat'))

    def test_same_actor_name_has_independent_binding_seats(self):
        self.launch();self.bind(0);self.bind(1)
        self.assertEqual(list(self.pointer(self.bindings[0]).result.input.held_buttons),[1])
        self.assertEqual(len(self.call(self.bindings[1],snapshot=True).result.snapshot.actors),0)
        self.assertEqual(list(self.pointer(self.bindings[1],target=self.other_target).result.input.held_buttons),[1])
        self.assertEqual(list(self.pointer(self.bindings[0],False).result.input.held_buttons),[])
        self.assertEqual(list(self.call(self.bindings[1],snapshot=True).result.snapshot.actors[0].held_buttons),[1])

    def test_retirement_releases_native_events_and_rejects_stale_input(self):
        self.launch();self.bind(0);self.pointer(self.bindings[0])
        command=core.RendererInputCommand(actor_id='same-actor',key=core.ActorKeyRequest(keysym='a',pressed=True))
        self.assertTrue(self.call(self.bindings[0],input=command).result.HasField('input'))
        self.assertTrue(self.call(self.bindings[0],retire=True).result.HasField('ready'))
        self.owner.operation(43,reply=True)
        events=[e[0]&127 for e in self.owner.events if e[0]&127 in (2,3,4,5)]
        self.assertEqual(events.count(2),1,events);self.assertEqual(events.count(3),1,events)
        self.assertEqual(events.count(4),1,events);self.assertEqual(events.count(5),1,events)
        self.assertEqual(self.pointer(self.bindings[0]).result.error.code,core.STALE_TARGET)
        self.assertEqual(self.call(self.bindings[0],bind_descriptor=0).result.error.code,core.STALE_TARGET)
        self.draw()

    def test_bindings_cannot_control_the_same_application_concurrently(self):
        self.launch();self.bind(0);self.bind(1)
        self.assertTrue(self.pointer(self.bindings[0]).result.HasField('input'))
        self.assertEqual(self.pointer(self.bindings[1]).result.error.code,core.BUSY)
        self.assertEqual(list(self.call(self.bindings[0],snapshot=True).result.snapshot.actors[0].held_buttons),[1])

    def test_all_binding_fields_must_match(self):
        self.launch();self.bind(0)
        for field,value in [('world_id','other'),('service_incarnation',b'z'*32),('authority_generation',2),('presentation_generation',9)]:
            wrong=native.WorldPresentationBinding();wrong.CopyFrom(self.bindings[0]);setattr(wrong,field,value)
            self.assertEqual(self.pointer(wrong).result.error.code,core.STALE_TARGET)
        self.assertTrue(self.pointer(self.bindings[0]).result.HasField('input'))

    def test_unsealed_and_invalid_bind_fail_without_descriptor_leaks(self):
        self.launch();before=self.call(descriptors=True).descriptors
        for _ in range(20):
            self.assertEqual(self.call(self.bindings[0],bind_descriptor=2).result.error.code,core.INVALID_ARGUMENT)
            invalid=native.WorldPresentationBinding();invalid.CopyFrom(self.bindings[0]);invalid.world_id=''
            self.assertEqual(self.call(invalid,bind_descriptor=0).result.error.code,core.INVALID_ARGUMENT)
        self.assertEqual(self.call(descriptors=True).descriptors,before)
        self.bind(0);self.draw()

    def test_other_native_server_cannot_supply_a_binding(self):
        self.launch(wrong_server=True)
        before=self.call(descriptors=True).descriptors
        self.assertEqual(self.call(self.bindings[0],bind_descriptor=3).result.error.code,core.FORBIDDEN)
        self.assertEqual(self.call(descriptors=True).descriptors,before)
        self.bind(0);self.draw()

    def test_first_scoped_binding_releases_held_legacy_input(self):
        self.launch()
        pointer=core.RendererInputCommand(actor_id='legacy',target=self.target,pixel=core.Pixel(x=1,y=1),
            pointer=core.ActorPointerRequest(button=1,pressed=True))
        key=core.RendererInputCommand(actor_id='legacy',key=core.ActorKeyRequest(keysym='a',pressed=True))
        self.assertTrue(self.call(unbound_input=pointer).result.HasField('input'))
        self.assertTrue(self.call(unbound_input=key).result.HasField('input'))
        self.bind(0)
        self.owner.operation(43,reply=True)
        events=[e[0]&127 for e in self.owner.events if e[0]&127 in (2,3,4,5)]
        for event in (2,3,4,5):self.assertEqual(events.count(event),1,events)
        self.assertTrue(self.pointer(self.bindings[0]).result.HasField('input'))
        self.assertEqual(self.call(unbound_input=pointer).result.error.code,core.UNIMPLEMENTED)

    def test_scoped_mode_disables_unbound_actor_input(self):
        self.launch();self.bind(0)
        result=self.call(unbound_input=core.RendererInputCommand(actor_id='same-actor',cancel=core.ActorStopRequest())).result
        self.assertEqual(result.error.code,core.UNIMPLEMENTED)
        self.assertTrue(self.pointer(self.bindings[0]).result.HasField('input'))

    def test_failed_retirement_does_not_acknowledge_revoked_live_seat(self):
        self.launch();self.bind(0);self.bind(1);self.pointer(self.bindings[0])
        self.supervisor.call(revoke=sec.Revoke(credential_id=self.grants[1].id))
        self.assertEqual(self.call(self.bindings[0],retire=True).result.error.code,core.UNAVAILABLE)
        self.assertTrue(self.pointer(self.bindings[1]).result.HasField('input'));self.draw()

if __name__=='__main__':unittest.main(verbosity=2)
