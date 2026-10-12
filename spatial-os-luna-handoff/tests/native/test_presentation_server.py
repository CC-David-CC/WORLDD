"""Exercise private world channels through the production spatialwm process."""
import array
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
import presentation_pb2 as protocol
import security_pb2 as sec
from harness.wire import frame
EXECUTABLE=Path(sys.argv.pop(1)).resolve()
import test_compositor_identity as fixture

class PresentationServerTests(unittest.TestCase):
    setUp=fixture.CompositorIdentityTests.setUp
    start=fixture.CompositorIdentityTests.start
    connect=fixture.CompositorIdentityTests.connect
    scene=fixture.CompositorIdentityTests.scene

    def config(self):
        grant=self.supervisor.issue(sec.COMPOSITOR,b'',b'')
        self.last_grant=grant
        value=native.NativeCompositorConfig(protocol_version=1,display_number=self.display,
            compositor_cookie=grant.cookie,server_id=self.supervisor.inspect().server_id,
            server_pid=self.server.pid,server_uid=os.getuid())
        fd=os.memfd_create('presentation-service-test',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        os.write(fd,value.SerializeToString())
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        return fd

    def receive(self,stream,kind):
        def exact(n):
            result=b''
            while len(result)<n:
                part=stream.recv(n-len(result));self.assertTrue(part,'Compositor channel closed');result+=part
            return result
        n=struct.unpack('!I',exact(4))[0];self.assertLessEqual(n,1048576)
        return kind.FromString(exact(n))

    def send(self,stream,value,descriptors=()):
        data=frame(value)
        ancillary=[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',descriptors))] if descriptors else []
        sent=stream.sendmsg([data],ancillary)
        stream.sendall(data[sent:])

    def control(self,descriptors=(),**operation):
        self.sequence+=1
        self.send(self.stream,protocol.PresentationControlRequest(protocol_version=1,request_id=self.sequence,**operation),descriptors)
        reply=self.receive(self.stream,protocol.PresentationControlReply)
        self.assertEqual(reply.request_id,self.sequence);return reply

    def launch(self):
        self.scene();self.sequence=0;self.worlds=[];self.expected_exit=0
        self.stream,child=socket.socketpair();self.stream.settimeout(10)
        fd=self.config();state=self.root/'renderer';state.mkdir()
        path=self.root/'presentation-server.log';log=path.open('wb')
        try:
            self.proc=subprocess.Popen([str(EXECUTABLE),'--state-dir',str(state),'--native-security-fd',str(fd),
                '--presentation-owner-pid',str(os.getpid())],stdin=child,stdout=child,stderr=log,
                pass_fds=(fd,),env=dict(os.environ,LIBGL_ALWAYS_SOFTWARE='1'))
        finally:child.close();os.close(fd)
        def cleanup():
            try:
                for binding,stream in self.worlds:
                    stream.close();self.control(detach=binding)
                if self.expected_exit==0:self.assertTrue(self.control(shutdown=protocol.PresentationEmpty()).shutting_down)
            finally:
                self.stream.close()
                try:self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3)
                log.close()
                diagnostic=path.read_text(errors='replace')
                if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                    target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                    (target/(self._testMethodName+'-presentation-server.log')).write_text(diagnostic)
                self.assertEqual(self.proc.returncode,self.expected_exit,diagnostic)
                for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,diagnostic)
        self.addCleanup(cleanup)
        self.assertTrue(self.receive(self.stream,protocol.PresentationControlReply).HasField('ready'))

    def attach(self,index):
        binding=native.WorldPresentationBinding(protocol_version=1,world_id='world-'+str(index),
            service_incarnation=bytes([index])*32,authority_generation=1,presentation_generation=index)
        stream,child=socket.socketpair();stream.settimeout(10);pidfd=os.pidfd_open(os.getpid())
        try:reply=self.control((child.fileno(),pidfd),attach=protocol.PresentationAttach(binding=binding,world_pid=os.getpid()))
        finally:child.close();os.close(pidfd)
        self.assertEqual(reply.state.stage,protocol.ATTACHED,reply)
        self.worlds.append((binding,stream));return binding,stream

    def hello(self,stream):
        fd=self.config()
        try:self.send(stream,protocol.PresentationWorldRequest(protocol_version=1,request_id=1,hello=protocol.PresentationEmpty()),(fd,))
        finally:os.close(fd)
        reply=self.receive(stream,core.RenderResult);self.assertTrue(reply.HasField('ready'),reply)

    def draw(self,stream,sequence,color):
        command=core.RenderCommand(protocol_version=1,request_id=sequence,width=64,height=64)
        command.scene.camera.position.SetInParent();command.scene.camera.orientation.w=1
        command.scene.config.CopyFrom(core.RenderConfig(vertical_fov=1.57079632679,near_clip=.01,far_clip=1000,
            background=core.Color(red=color[0],green=color[1],blue=color[2]),barrier_color=core.Color(red=1,green=2,blue=3)))
        self.send(stream,protocol.PresentationWorldRequest(protocol_version=1,request_id=sequence,command=command))
        return self.receive(stream,core.RenderResult)

    def pixel(self,xid):
        head,body=self.supervisor.operation(73,self.supervisor.pack('IhhHHI',xid,1,1,1,1,0xffffffff),2,True)
        return int.from_bytes(body,'little')&0xffffff

    def test_selection_changes_real_pixels_without_restarting_compositor(self):
        self.launch();a,sa=self.attach(1);b,sb=self.attach(2);self.hello(sa);self.hello(sb)
        self.control(select=a)
        first=self.draw(sa,2,(17,34,51));self.assertTrue(first.HasField('frame'),first)
        self.assertEqual(self.pixel(first.frame.xid),0x112233)
        self.control(select=b)
        self.assertEqual(self.pixel(first.frame.xid),0)
        denied=self.draw(sa,3,(255,0,0));self.assertEqual(denied.error.code,core.FORBIDDEN)
        second=self.draw(sb,2,(51,34,17));self.assertTrue(second.HasField('frame'),second)
        self.assertEqual(second.frame.xid,first.frame.xid)
        self.assertGreater(second.frame.frame_id,first.frame.frame_id)
        self.assertEqual(self.pixel(second.frame.xid),0x332211)
        self.assertIsNone(self.proc.poll())

    def test_worlds_initialize_in_reverse_reservation_order(self):
        self.launch();a,sa=self.attach(1);b,sb=self.attach(2)
        self.hello(sb);self.hello(sa)
        self.assertTrue(self.control(select=a).state.selected)
        self.assertTrue(self.control(select=b).state.selected)
        self.assertFalse(self.control(inspect=a).state.selected)

    def test_pending_world_cannot_be_selected(self):
        self.launch();a,sa=self.attach(1)
        self.assertEqual(self.control(select=a).error.code,core.UNAVAILABLE)
        self.hello(sa);self.assertTrue(self.control(select=a).state.selected)

    def test_mismatched_binding_does_not_select_world(self):
        self.launch();a,sa=self.attach(1);self.hello(sa)
        wrong=native.WorldPresentationBinding();wrong.CopyFrom(a);wrong.authority_generation+=1
        self.assertEqual(self.control(select=wrong).error.code,core.STALE_TARGET)
        self.assertTrue(self.control(select=a).state.selected)

    def test_malformed_world_is_retired_without_losing_other_world(self):
        self.launch();a,sa=self.attach(1);b,sb=self.attach(2);self.hello(sa);self.hello(sb)
        sa.sendall(struct.pack('!I',1)+b'\xff')
        self.assertEqual(sa.recv(1),b'')
        self.assertEqual(self.control(inspect=a).state.stage,protocol.RETIRED)
        self.assertTrue(self.control(select=b).state.selected)

    def retired(self,binding):
        deadline=time.monotonic()+8
        while True:
            reply=self.control(inspect=binding)
            if reply.state.stage==protocol.RETIRED:return reply.state
            self.assertLess(time.monotonic(),deadline,reply)
            time.sleep(.01)

    def test_replayed_request_retires_only_its_world(self):
        self.launch();a,sa=self.attach(1);b,sb=self.attach(2);self.hello(sa);self.hello(sb)
        self.send(sa,protocol.PresentationWorldRequest(protocol_version=1,request_id=1,
            command=core.RenderCommand(protocol_version=1,request_id=1,sync=core.RendererSyncRequest())))
        self.assertEqual(sa.recv(1),b'')
        self.assertFalse(self.retired(a).selected)
        self.assertTrue(self.control(select=b).state.selected)

    def test_partial_frame_deadline_preserves_other_world(self):
        self.launch();a,sa=self.attach(1);b,sb=self.attach(2);self.hello(sa);self.hello(sb)
        start=time.monotonic();sa.sendall(b'\x00')
        self.assertTrue(self.control(select=b).state.selected)
        self.assertTrue(self.draw(sb,2,(17,34,51)).HasField('frame'))
        self.assertLess(time.monotonic()-start,3)
        self.assertEqual(sa.recv(1),b'')
        self.assertGreaterEqual(time.monotonic()-start,4.9)
        self.assertLess(time.monotonic()-start,8)
        self.retired(a)
        self.assertTrue(self.draw(sb,3,(51,34,17)).HasField('frame'))

    def test_disconnect_clears_selected_pixels(self):
        self.launch();a,sa=self.attach(1);self.hello(sa);self.control(select=a)
        result=self.draw(sa,2,(17,34,51));self.assertTrue(result.HasField('frame'),result)
        sa.close();self.assertFalse(self.retired(a).selected)
        self.assertEqual(self.pixel(result.frame.xid),0)

    def test_wrong_process_handle_rejected_without_consuming_generation(self):
        self.launch()
        binding=native.WorldPresentationBinding(protocol_version=1,world_id='world-1',
            service_incarnation=b'a'*32,authority_generation=1,presentation_generation=1)
        stream,child=socket.socketpair();fd=os.memfd_create('not-a-pidfd',os.MFD_CLOEXEC)
        try:reply=self.control((child.fileno(),fd),attach=protocol.PresentationAttach(binding=binding,world_pid=os.getpid()))
        finally:stream.close();child.close();os.close(fd)
        self.assertTrue(reply.HasField('error'),reply)
        a,sa=self.attach(1);self.hello(sa);self.assertTrue(self.control(select=a).state.selected)

    def test_missing_hello_descriptor_retires_world(self):
        self.launch();a,sa=self.attach(1)
        self.send(sa,protocol.PresentationWorldRequest(protocol_version=1,request_id=1,hello=protocol.PresentationEmpty()))
        self.assertEqual(sa.recv(1),b'');self.retired(a)

    def test_foreign_sender_on_inherited_socket_retires_world(self):
        self.launch();a,sa=self.attach(1);self.hello(sa)
        child=os.fork()
        if child==0:
            try:self.send(sa,protocol.PresentationWorldRequest(protocol_version=1,request_id=2,
                command=core.RenderCommand(protocol_version=1,request_id=2,sync=core.RendererSyncRequest())))
            finally:os._exit(0)
        _,status=os.waitpid(child,0);self.assertEqual(status,0)
        try:self.assertEqual(sa.recv(1),b'')
        except ConnectionResetError:pass
        self.retired(a)

    def test_world_exit_retires_channel_even_with_surviving_socket_copy(self):
        self.launch()
        binding=native.WorldPresentationBinding(protocol_version=1,world_id='child-world',
            service_incarnation=b'c'*32,authority_generation=1,presentation_generation=1)
        stream,server=socket.socketpair();fd=self.config()
        child_code = """import array,os,socket,struct,sys,time
import presentation_pb2 as p
import core_pb2 as c
from harness.wire import frame
s=socket.socket(fileno=int(sys.argv[1]));fd=int(sys.argv[2])
sys.stdin.buffer.read(1)
data=frame(p.PresentationWorldRequest(protocol_version=1,request_id=1,hello=p.PresentationEmpty()))
sent=s.sendmsg([data],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))]);s.sendall(data[sent:]);os.close(fd)
def exact(n):
 r=b''
 while len(r)<n:
  part=s.recv(n-len(r))
  if not part:raise RuntimeError('closed')
  r+=part
 return r
reply=c.RenderResult.FromString(exact(struct.unpack('!I',exact(4))[0]))
assert reply.HasField('ready'),reply
print('ready',flush=True)
sys.stdin.buffer.read(1)
"""
        child=subprocess.Popen([sys.executable,'-c',child_code,str(stream.fileno()),str(fd)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(stream.fileno(),fd))
        os.close(fd)
        def reap():
            if child.poll() is None:child.kill()
            child.communicate(timeout=3)
        self.addCleanup(reap)
        pidfd=os.pidfd_open(child.pid)
        try:reply=self.control((server.fileno(),pidfd),attach=protocol.PresentationAttach(binding=binding,world_pid=child.pid))
        finally:server.close();os.close(pidfd)
        self.assertEqual(reply.state.stage,protocol.ATTACHED,reply)
        self.worlds.append((binding,stream))
        child.stdin.write(b'g');child.stdin.flush()
        import select
        self.assertTrue(select.select([child.stdout],[],[],8)[0],'Child did not initialize')
        self.assertEqual(child.stdout.readline(),b'ready\n')
        self.assertTrue(self.control(select=binding).state.selected)
        child.kill();child.wait(timeout=3)
        self.assertFalse(self.retired(binding).selected)
        self.assertIsNone(self.proc.poll())

    def world_command(self,stream,sequence,**operation):
        command=core.RenderCommand(protocol_version=1,request_id=sequence,**operation)
        self.send(stream,protocol.PresentationWorldRequest(protocol_version=1,request_id=sequence,command=command))
        return self.receive(stream,core.RenderResult)

    def hold_input(self,stream):
        self.owner.operation(2,self.owner.pack('III',self.window,1<<11,15))
        sequence=1;deadline=time.monotonic()+5
        while True:
            sequence+=1;reply=self.world_command(stream,sequence,sync=core.RendererSyncRequest())
            self.assertTrue(reply.HasField('snapshot'),reply)
            found=[s for s in reply.snapshot.surfaces if s.xid==self.window and s.mapped]
            if found:break
            self.assertLess(time.monotonic(),deadline);time.sleep(.01)
        sequence+=1
        reply=self.world_command(stream,sequence,input=core.RendererInputCommand(actor_id='actor',target=found[0].handle,
            pixel=core.Pixel(x=1,y=1),pointer=core.ActorPointerRequest(button=1,pressed=True)))
        self.assertTrue(reply.HasField('input'),reply);self.assertEqual(list(reply.input.held_buttons),[1])
        sequence+=1
        reply=self.world_command(stream,sequence,input=core.RendererInputCommand(actor_id='actor',
            key=core.ActorKeyRequest(keysym='a',pressed=True)))
        self.assertTrue(reply.HasField('input'),reply)
        return sequence

    def balanced_events(self):
        self.owner.operation(43,reply=True)
        events=[e[0]&127 for e in self.owner.events if e[0]&127 in (2,3,4,5)]
        for event in (2,3,4,5):self.assertEqual(events.count(event),1,events)

    def test_detach_releases_held_native_key_and_button(self):
        self.launch();a,sa=self.attach(1);self.hello(sa);self.hold_input(sa)
        reply=self.control(detach=a);self.assertEqual(reply.state.stage,protocol.RETIRED,reply)
        self.balanced_events()
        self.assertEqual(self.control(select=a).error.code,core.STALE_TARGET)
        self.assertEqual(sa.recv(1),b'')

    def test_world_disconnect_releases_held_native_key_and_button(self):
        self.launch();a,sa=self.attach(1);self.hello(sa);self.hold_input(sa)
        sa.close();self.retired(a);self.balanced_events()

    def test_revoked_input_reports_uncertain_retirement_and_keeps_graphics_live(self):
        self.launch();a,sa=self.attach(1);b,sb=self.attach(2)
        self.hello(sa);grant=self.last_grant;self.hello(sb);self.hold_input(sa)
        self.assertEqual(self.supervisor.call(revoke=sec.Revoke(credential_id=grant.id)).code,sec.OK)
        reply=self.control(detach=a);self.assertEqual(reply.state.stage,protocol.UNCERTAIN,reply)
        self.assertEqual(self.control(inspect=a).state.stage,protocol.UNCERTAIN)
        self.assertEqual(self.control(select=a).error.code,core.UNAVAILABLE)
        self.assertTrue(self.control(select=b).state.selected)
        self.assertTrue(self.draw(sb,2,(17,34,51)).HasField('frame'))
        self.assertEqual(self.control(shutdown=protocol.PresentationEmpty()).error.code,core.IN_USE)
        # No supervisor cleanup receipt exists yet. Owner loss must end the process,
        # rather than turn unconfirmed cleanup into a successful retirement.
        self.expected_exit=2

    def test_owner_exit_ends_compositor_with_control_socket_still_open(self):
        self.scene();fd=self.config();state=self.root/'owner-exit';state.mkdir()
        report,child_report=socket.socketpair();report.settimeout(10)
        path=self.root/'owner-exit.log';log=path.open('wb')
        code="""import array,os,socket,struct,subprocess,sys
report=socket.socket(fileno=int(sys.argv[1]));fd=int(sys.argv[2])
stream,child=socket.socketpair()
p=subprocess.Popen([sys.argv[3],'--state-dir',sys.argv[4],'--native-security-fd',str(fd),
 '--presentation-owner-pid',str(os.getpid())],stdin=child,stdout=child,pass_fds=(fd,))
child.close();os.close(fd)
def exact(n):
 r=b''
 while len(r)<n:
  part=stream.recv(n-len(r))
  if not part:raise RuntimeError('compositor closed')
  r+=part
 return r
exact(struct.unpack('!I',exact(4))[0])
report.sendmsg([str(p.pid).encode()],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[stream.fileno()]))])
sys.stdin.buffer.read(1)
os._exit(0)
"""
        owner=subprocess.Popen([sys.executable,'-c',code,str(child_report.fileno()),str(fd),str(EXECUTABLE),str(state)],
            stdin=subprocess.PIPE,stdout=log,stderr=log,pass_fds=(child_report.fileno(),fd),
            env=dict(os.environ,LIBGL_ALWAYS_SOFTWARE='1'))
        child_report.close();os.close(fd)
        retained=None;pidfd=None
        try:
            data,ancillary,flags,address=report.recvmsg(64,socket.CMSG_SPACE(4))
            self.assertTrue(data);self.assertEqual(flags,0)
            handles=array.array('i');handles.frombytes(ancillary[0][2]);self.assertEqual(len(handles),1)
            retained=socket.socket(fileno=handles[0]);pidfd=os.pidfd_open(int(data))
            owner.stdin.write(b'x');owner.stdin.flush();self.assertEqual(owner.wait(timeout=3),0)
            import select
            self.assertTrue(select.select([pidfd],[],[],5)[0],'Compositor survived its control owner')
            diagnostic=path.read_text(errors='replace')
            self.assertIn('Presentation owner exited',diagnostic)
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,diagnostic)
        finally:
            if owner.poll() is None:owner.kill()
            owner.communicate(timeout=3)
            if pidfd is not None:
                import select,signal
                if not select.select([pidfd],[],[],0)[0]:signal.pidfd_send_signal(pidfd,signal.SIGKILL)
                os.close(pidfd)
            if retained is not None:retained.close()
            report.close();log.close()
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-owner-exit.log')).write_bytes(path.read_bytes())

if __name__=='__main__':unittest.main(verbosity=2)

