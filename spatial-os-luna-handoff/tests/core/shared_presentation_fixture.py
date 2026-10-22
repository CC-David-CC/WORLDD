"""Launch the production shared compositor beside an authenticated world fixture."""
import array
import fcntl
import os
from pathlib import Path
import socket
import subprocess
import time
import compositor_native_pb2 as native
import presentation_pb2 as presentation
import security_pb2 as security
from harness.wire import frame
from test_world_identity import receive


class SharedPresentationFixture:
    def __init__(self,fixture,state,world_info,executable):
        self.fixture=fixture;self.world=None;self.proc=None;self.sequence=0;self.attached=False
        self.channel,self.child=socket.socketpair();self.channel.settimeout(10)
        self.world_channel,self.server_channel=socket.socketpair()
        for channel in [self.world_channel,self.server_channel]:
            channel.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        self.pidfd=None;self.native_fd=None;self.log=None
        fixture.addCleanup(self.close)
        grant=fixture.native.supervisor.issue(security.COMPOSITOR,b'',b'')
        config=native.NativeCompositorConfig(protocol_version=1,display_number=fixture.native.display,
            compositor_cookie=grant.cookie,server_id=fixture.server_id,
            server_pid=fixture.native.server.pid,server_uid=os.getuid())
        self.native_fd=os.memfd_create('shared-seat-query',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        os.write(self.native_fd,config.SerializeToString(deterministic=True))
        fcntl.fcntl(self.native_fd,fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        self.path=fixture.root/'shared-presentation.log';self.log=self.path.open('wb')
        renderer_state=state.parent/'shared-renderer';renderer_state.mkdir(mode=0o700)
        self.proc=subprocess.Popen([str(executable),'--state-dir',str(renderer_state),
            '--native-security-fd',str(self.native_fd),'--presentation-owner-pid',str(os.getpid())],
            stdin=self.child,stdout=self.child,stderr=self.log,pass_fds=(self.native_fd,),
            env=dict(os.environ,LIBGL_ALWAYS_SOFTWARE='1'))
        self.child.close();self.pidfd=os.pidfd_open(self.proc.pid)
        fixture.assertTrue(self.read().HasField('ready'))
        self.binding=native.WorldPresentationBinding(protocol_version=1,world_id=world_info['world_id'],
            service_incarnation=os.urandom(32),authority_generation=1,presentation_generation=1)

    def read(self):
        return receive(self.channel.recv,self.channel,presentation.PresentationControlReply)

    def control(self,descriptors=(),**operation):
        self.sequence+=1
        request=presentation.PresentationControlRequest(protocol_version=1,request_id=self.sequence,**operation)
        raw=frame(request)
        ancillary=[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',descriptors))] if descriptors else []
        sent=self.channel.sendmsg([raw],ancillary);self.channel.sendall(raw[sent:])
        reply=self.read();self.fixture.assertEqual(reply.request_id,self.sequence);return reply

    def world_arguments(self):
        return ['--native-security-fd',str(self.native_fd),'--presentation-fd',str(self.world_channel.fileno()),
            '--presentation-pidfd',str(self.pidfd),'--presentation-pid',str(self.proc.pid),
            '--presentation-owner-pid',str(os.getpid())]

    def world_descriptors(self):
        return self.native_fd,self.world_channel.fileno(),self.pidfd

    def attach(self,world):
        self.world=world;pidfd=os.pidfd_open(world.proc.pid)
        try:reply=self.control((self.server_channel.fileno(),pidfd),
            attach=presentation.PresentationAttach(binding=self.binding,world_pid=world.proc.pid))
        finally:os.close(pidfd)
        self.fixture.assertEqual(reply.state.stage,presentation.ATTACHED,reply);self.attached=True
        self.world_channel.close();self.server_channel.close()
        deadline=time.monotonic()+5
        while True:
            reply=self.control(inspect=self.binding)
            if reply.state.stage==presentation.READY:break
            self.fixture.assertLess(time.monotonic(),deadline,reply);time.sleep(.01)
        self.fixture.assertTrue(self.control(select=self.binding).state.selected)

    def close(self):
        if self.proc is None:
            for channel in [self.channel,self.child,self.world_channel,self.server_channel]:channel.close()
            for name in ['pidfd','native_fd']:
                fd=getattr(self,name,None)
                if fd is not None:os.close(fd);setattr(self,name,None)
            if self.log is not None:self.log.close()
            return
        process=self.proc;self.proc=None
        try:
            if self.world is not None:self.world.close()
            if process.poll() is None:
                if self.attached:self.control(detach=self.binding)
                self.fixture.assertTrue(self.control(shutdown=presentation.PresentationEmpty()).shutting_down)
                self.fixture.assertEqual(process.wait(timeout=5),0)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=3)
            for channel in [self.channel,self.child,self.world_channel,self.server_channel]:channel.close()
            for name in ['pidfd','native_fd']:
                fd=getattr(self,name,None)
                if fd is not None:os.close(fd);setattr(self,name,None)
            if self.log is not None:self.log.close()
            diagnostic=self.path.read_text(errors='replace')
            target=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
            if target:
                path=Path(target);path.mkdir(parents=True,exist_ok=True)
                (path/(self.fixture._testMethodName+'-shared-presentation.log')).write_text(diagnostic)
            self.fixture.assertEqual(process.returncode,0,diagnostic)
            for marker in ['AddressSanitizer','LeakSanitizer','runtime error:']:
                self.fixture.assertNotIn(marker,diagnostic)
