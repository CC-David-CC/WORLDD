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
import broker_pb2 as broker
import process_scope_pb2 as scopes
import native_scope_pb2 as native_scope
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

    def draw(self,stream,sequence,color,capture_fd=None):
        command=core.RenderCommand(protocol_version=1,request_id=sequence,width=64,height=64,capture=capture_fd is not None)
        command.scene.camera.position.SetInParent();command.scene.camera.orientation.w=1
        command.scene.config.CopyFrom(core.RenderConfig(vertical_fov=1.57079632679,near_clip=.01,far_clip=1000,
            background=core.Color(red=color[0],green=color[1],blue=color[2]),barrier_color=core.Color(red=1,green=2,blue=3)))
        self.send(stream,protocol.PresentationWorldRequest(protocol_version=1,request_id=sequence,command=command),
            () if capture_fd is None else (capture_fd,))
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

    def shared_worldd(self,index):
        binding=native.WorldPresentationBinding(protocol_version=1,world_id='fixture-world-'+str(index),
            service_incarnation=bytes([index])*32,authority_generation=1,presentation_generation=index)
        client,server=socket.socketpair()
        server.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        client.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        manager,child_manager=socket.socketpair();manager.settimeout(12)
        native_fd=self.config();renderer_pidfd=os.pidfd_open(self.proc.pid)
        state=self.root/('worldd-'+str(index));state.mkdir();path=self.root/('worldd-'+str(index)+'.log');log=path.open('wb')
        descriptors=(client.fileno(),native_fd,renderer_pidfd)
        try:
            process=subprocess.Popen([str(EXECUTABLE.parent/'worldd'),'--state-dir',str(state),
                '--native-security-fd',str(native_fd),'--presentation-fd',str(client.fileno()),
                '--presentation-pidfd',str(renderer_pidfd),'--presentation-pid',str(self.proc.pid),
                '--presentation-owner-pid',str(os.getpid())],stdin=child_manager,stdout=child_manager,stderr=log,
                pass_fds=descriptors,env=dict(os.environ,LIBGL_ALWAYS_SOFTWARE='1'))
        finally:child_manager.close();client.close();os.close(native_fd);os.close(renderer_pidfd)
        def cleanup():
            if manager.fileno()>=0:manager.shutdown(socket.SHUT_WR)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
            manager.close()
            log.close();diagnostic=path.read_text(errors='replace')
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-worldd-'+str(index)+'.log')).write_text(diagnostic)
            self.assertEqual(process.returncode,0,diagnostic)
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,diagnostic)
        self.addCleanup(cleanup)
        pidfd=os.pidfd_open(process.pid)
        try:reply=self.control((server.fileno(),pidfd),attach=protocol.PresentationAttach(binding=binding,world_pid=process.pid))
        finally:server.close();os.close(pidfd)
        self.assertEqual(reply.state.stage,protocol.ATTACHED,reply)
        self.worlds.append((binding,client))
        return binding,manager,process

    def test_real_worldd_connects_and_stops_without_terminating_shared_compositor(self):
        self.launch();a,ma,pa=self.shared_worldd(1);b,mb,pb=self.shared_worldd(2)
        for channel in (ma,mb):
            self.send(channel,core.Request(protocol_version=1,request_id=1,world_info=core.WorldInfoRequest()))
            reply=self.receive(channel,core.Reply);self.assertTrue(reply.HasField('world_info'),reply)
        frames=[];captures=[]
        for index,(binding,channel) in enumerate(((a,ma),(b,mb)),1):
            opened=core.ActorOpenRequest(id='fixture-actor',grants=['navigate'])
            opened.pose.position.SetInParent();opened.pose.orientation.w=1
            self.send(channel,core.Request(protocol_version=1,request_id=2,actor_open=opened))
            reply=self.receive(channel,core.Reply);self.assertTrue(reply.HasField('actor_opened'),reply)
            token=reply.actor_opened.token
            self.assertTrue(self.control(select=binding).state.selected)
            self.send(channel,core.Request(protocol_version=1,request_id=3,
                render_present=core.RenderRequest(token=token,width=64,height=64)))
            reply=self.receive(channel,core.Reply);self.assertTrue(reply.HasField('frame'),reply);frames.append(reply.frame)
            self.send(channel,core.Request(protocol_version=1,request_id=4,
                render_capture=core.RenderRequest(token=token,width=64,height=64)))
            reply=self.receive(channel,core.Reply);self.assertTrue(reply.HasField('frame'),reply)
            path=Path(reply.frame.path).resolve();captures.append(path)
            self.assertTrue(path.is_relative_to((self.root/('worldd-'+str(index))).resolve()))
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            data=path.read_bytes();self.assertTrue(data.startswith(b'P6\n64 64\n255\n'))
            pixels=data[len(b'P6\n64 64\n255\n'):];self.assertEqual(len(pixels),64*64*3)
            self.assertEqual(int.from_bytes(pixels[3*(64+1):3*(64+2)],'big'),self.pixel(reply.frame.xid))
        self.assertNotEqual(captures[0].parent,captures[1].parent)
        self.assertEqual(list((self.root/'renderer'/'captures').iterdir()),[])
        self.assertEqual(frames[0].xid,frames[1].xid)
        self.assertGreater(frames[1].frame_id,frames[0].frame_id)
        ma.shutdown(socket.SHUT_WR);self.assertEqual(pa.wait(timeout=5),0);ma.close()
        self.retired(a);self.assertIsNone(self.proc.poll())
        self.assertTrue(self.control(select=b).state.selected)
        self.send(mb,core.Request(protocol_version=1,request_id=5,runtime_fence=core.RuntimeFenceRequest()))
        reply=self.receive(mb,core.Reply);self.assertFalse(reply.HasField('error'),reply)
        self.assertIsNone(pb.poll());self.assertIsNone(self.proc.poll())

    def test_capture_rejects_nonregular_descriptor_and_keeps_world_usable(self):
        self.launch();a,sa=self.attach(1);self.hello(sa);self.control(select=a)
        read_fd,write_fd=os.pipe()
        try:reply=self.draw(sa,2,(17,34,51),capture_fd=write_fd)
        finally:os.close(read_fd);os.close(write_fd)
        self.assertEqual(reply.error.code,core.INVALID_ARGUMENT,reply)
        self.assertTrue(self.draw(sa,3,(17,34,51)).HasField('frame'))

    def test_attached_scope_rejects_another_native_credential(self):
        self.launch()
        binding=native.WorldPresentationBinding(protocol_version=1,world_id='scope-world',service_incarnation=b'r'*32,
            authority_generation=1,presentation_generation=1)
        expected=self.config();grant=self.last_grant;os.close(expected)
        owner=broker.ControlOwner(issuer_id=b'i'*32)
        owner.launch.participant_id=b'p'*32;owner.launch.registered_revision=1;owner.launch.generation=1
        owner.launch.service_incarnation=b'r'*32;owner.launch.launch_id=b'l'*32
        owner.launch.supervision_revision=1;owner.launch.authorization_id=b'a'*32
        owner.native.CopyFrom(native_scope.NativeScopeIdentity(launch_id=b'l'*32,
            server_id=self.supervisor.inspect().server_id,credential_id=grant.id,role=sec.COMPOSITOR))
        scope=scopes.ProcessScopeIdentity(launch_id=b'l'*32,scope_name='authority-'+(b'l'*32).hex(),
            boot_id='0'*36,device=1,inode=1)
        client,server=socket.socketpair();client.settimeout(10);pidfd=os.pidfd_open(os.getpid())
        try:
            reply=self.control((server.fileno(),pidfd),attach=protocol.PresentationAttach(
                binding=binding,world_pid=os.getpid(),owner=owner,process=scope))
        finally:server.close();os.close(pidfd)
        self.assertEqual(reply.state.stage,protocol.ATTACHED,reply);self.worlds.append((binding,client))
        wrong=self.config()
        try:self.send(client,protocol.PresentationWorldRequest(protocol_version=1,request_id=1,
            hello=protocol.PresentationEmpty()),(wrong,))
        finally:os.close(wrong)
        reply=self.receive(client,core.RenderResult)
        self.assertEqual(reply.error.code,core.FORBIDDEN,reply)
        self.assertEqual(self.control(inspect=binding).state.stage,protocol.ATTACHED)
        self.assertEqual(self.control(select=binding).error.code,core.UNAVAILABLE)

    def test_checked_scope_receipts_resolve_uncertain_retirement(self):
        self.launch()
        self.owner.operation(2,self.owner.pack('III',self.window,1<<11,15))
        binding=native.WorldPresentationBinding(protocol_version=1,world_id='receipt-world',service_incarnation=b'r'*32,
            authority_generation=1,presentation_generation=1)
        config=self.config();grant=self.last_grant
        owner=broker.ControlOwner(issuer_id=b'i'*32)
        owner.launch.participant_id=b'p'*32;owner.launch.registered_revision=1;owner.launch.generation=1
        owner.launch.service_incarnation=b'r'*32;owner.launch.launch_id=b'l'*32
        owner.launch.supervision_revision=1;owner.launch.authorization_id=b'a'*32
        owner.native.CopyFrom(native_scope.NativeScopeIdentity(launch_id=b'l'*32,
            server_id=self.supervisor.inspect().server_id,credential_id=grant.id,role=sec.COMPOSITOR))
        scope=scopes.ProcessScopeIdentity(launch_id=b'l'*32,scope_name='authority-'+(b'l'*32).hex(),
            boot_id='0'*36,device=1,inode=1)
        client,server=socket.socketpair();server.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        gate_read,gate_write=os.pipe();ready_read,ready_write=os.pipe()
        child=os.fork()
        if child==0:
            try:
                os.close(gate_write);os.close(ready_read);server.close();os.read(gate_read,1)
                self.send(client,protocol.PresentationWorldRequest(protocol_version=1,request_id=1,
                    hello=protocol.PresentationEmpty()),(config,));os.close(config)
                result=self.receive(client,core.RenderResult)
                if not result.HasField('ready'):raise RuntimeError(str(result))
                sequence=1;deadline=time.monotonic()+5
                while True:
                    sequence+=1;result=self.world_command(client,sequence,sync=core.RendererSyncRequest())
                    found=[value for value in result.snapshot.surfaces if value.xid==self.window and value.mapped]
                    if found:break
                    if time.monotonic()>=deadline:raise RuntimeError('Missing surface')
                    time.sleep(.01)
                sequence+=1;result=self.world_command(client,sequence,input=core.RendererInputCommand(actor_id='held',
                    target=found[0].handle,pixel=core.Pixel(x=1,y=1),pointer=core.ActorPointerRequest(button=1,pressed=True)))
                if not result.HasField('input'):raise RuntimeError(str(result))
                sequence+=1;result=self.world_command(client,sequence,input=core.RendererInputCommand(actor_id='held',
                    key=core.ActorKeyRequest(keysym='a',pressed=True)))
                if not result.HasField('input'):raise RuntimeError(str(result))
                os.write(ready_write,b'R');os.read(gate_read,1);os._exit(0)
            except BaseException:os.write(ready_write,b'E');os._exit(1)
        os.close(config);os.close(gate_read);os.close(ready_write);client.close()
        reaped=[False]
        def cleanup_child():
            if not reaped[0]:
                os.kill(child,9);os.waitpid(child,0)
            os.close(gate_write);os.close(ready_read)
        self.addCleanup(cleanup_child)
        pidfd=os.pidfd_open(child)
        try:reply=self.control((server.fileno(),pidfd),attach=protocol.PresentationAttach(binding=binding,world_pid=child,owner=owner,process=scope))
        finally:server.close();os.close(pidfd)
        self.assertEqual(reply.state.stage,protocol.ATTACHED,reply);self.worlds.append((binding,client))
        os.write(gate_write,b'G')
        import select
        self.assertTrue(select.select([ready_read],[],[],8)[0]);self.assertEqual(os.read(ready_read,1),b'R')
        self.assertEqual(self.supervisor.call(revoke=sec.Revoke(credential_id=grant.id)).code,sec.OK)
        self.assertEqual(self.control(detach=binding).state.stage,protocol.UNCERTAIN)
        recovery=protocol.PresentationRecovery(binding=binding,
            process_stopped=scopes.ProcessScopeStopped(identity=scope),native_stopped=native_scope.NativeScopeStopped(identity=owner.native))
        self.assertEqual(self.control(recover=recovery).error.code,core.INVALID_ARGUMENT)
        os.write(gate_write,b'Q');_,status=os.waitpid(child,0);reaped[0]=True;self.assertEqual(status,0)
        wrong=protocol.PresentationRecovery();wrong.CopyFrom(recovery);wrong.native_stopped.identity.credential_id=b'x'*32
        self.assertEqual(self.control(recover=wrong).error.code,core.INVALID_ARGUMENT)
        wrong_process=protocol.PresentationRecovery();wrong_process.CopyFrom(recovery)
        wrong_process.process_stopped.identity.inode+=1
        self.assertEqual(self.control(recover=wrong_process).error.code,core.INVALID_ARGUMENT)
        missing=protocol.PresentationRecovery();missing.CopyFrom(recovery);missing.ClearField('native_stopped')
        self.assertEqual(self.control(recover=missing).error.code,core.INVALID_ARGUMENT)
        self.assertEqual(self.control(inspect=binding).state.stage,protocol.UNCERTAIN)
        self.assertEqual(self.control(recover=recovery).state.stage,protocol.RETIRED)
        self.assertEqual(self.control(recover=recovery).state.stage,protocol.RETIRED)
        self.balanced_events()
        b,sb=self.attach(2);self.hello(sb);self.assertTrue(self.control(select=b).state.selected)
        self.assertTrue(self.draw(sb,2,(17,34,51)).HasField('frame'))

if __name__=='__main__':unittest.main(verbosity=2)

