"""A test guardian separates world registration from issuer fencing."""
import fcntl
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import sys
import time
import unittest

WORKER=str(Path(sys.argv.pop(3)).resolve())
import test_scoped_input as base
import compositor_native_pb2 as config
import core_pb2 as core
import input_pb2 as ip
import native_seat_guardian_pb2 as guardian
from harness.wire import frame

ISSUER_PROGRAM=r'''
import os,socket,struct,sys
s=socket.socket(fileno=int(sys.argv[1]))
def exact(read,n):
 data=b''
 while len(data)<n:
  part=read(n-len(data))
  if not part:raise EOFError()
  data+=part
 return data
try:
 n=struct.unpack('!I',exact(s.recv,4))[0];exact(s.recv,n);os.write(1,b'R')
 header=exact(lambda n:os.read(0,n),4);n=struct.unpack('!I',header)[0]
 s.sendall(header+exact(lambda n:os.read(0,n),n))
 header=exact(s.recv,4);n=struct.unpack('!I',header)[0];data=exact(s.recv,n)
 os.write(1,header+data);os._exit(0)
except BaseException:os._exit(2)
'''
class NativeSeatGuardianRolesTests(base.ScopedInputTests):
    def receive(self,reader,message):
        def exact(size):
            data=b'';deadline=time.monotonic()+5
            while len(data)<size:
                left=deadline-time.monotonic();self.assertGreater(left,0)
                self.assertTrue(select.select([reader],[],[],left)[0],'Guardian reply timed out')
                part=reader.recv(size-len(data)) if isinstance(reader,socket.socket) else os.read(reader.fileno(),size-len(data))
                self.assertTrue(part,'Guardian reply closed');data+=part
            return data
        size=struct.unpack('!I',exact(4))[0];self.assertLessEqual(size,1048576)
        return message.FromString(exact(size))
    def start_guardian(self,*,same_sender=False,expect_ready=True,hold_after_failure=False):
        identity=self.supervisor.inspect()
        value=config.NativeCompositorConfig(protocol_version=1,display_number=self.display,
            compositor_cookie=self.credential.cookie,server_id=identity.server_id,server_pid=self.server.pid,server_uid=os.getuid())
        fd=os.memfd_create('guardian-config',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);os.write(fd,value.SerializeToString())
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        world,world_child=socket.socketpair();issuer_parent,issuer_child=socket.socketpair()
        issuer=subprocess.Popen([sys.executable,'-B','-c',ISSUER_PROGRAM,str(issuer_parent.fileno())],
            pass_fds=[issuer_parent.fileno()],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        issuer_parent.close();log=(self.root/'guardian-roles.log').open('wb')
        try:
            worker=subprocess.Popen([WORKER,str(fd),str(world_child.fileno()),str(issuer_child.fileno()),
                str(self.root/'guardian.sqlite'),str(os.getpid()),str(os.getpid()),str(os.getpid() if same_sender else issuer.pid)]
                +(['--hold-after-failure'] if hold_after_failure else []),
                pass_fds=[fd,world_child.fileno(),issuer_child.fileno()],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log)
        finally:os.close(fd);world_child.close();issuer_child.close()
        def cleanup():
            world.close()
            if not issuer.stdin.closed:issuer.stdin.close()
            for proc in (worker,issuer):
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            issuer.stdout.close();log.close();text=(self.root/'guardian-roles.log').read_text(errors='replace')
            for marker in ('AddressSanitizer','LeakSanitizer','ThreadSanitizer','runtime error:'):self.assertNotIn(marker,text)
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-guardian-roles.log')).write_text(text)
        self.addCleanup(cleanup)
        if expect_ready:
            self.assertEqual(self.receive(world,guardian.GuardianReply).request_id,0)
            self.assertTrue(select.select([issuer.stdout],[],[],5)[0]);self.assertEqual(os.read(issuer.stdout.fileno(),1),b'R')
        return worker,world,issuer,identity
    def registration(self,identity,seat):
        binding=guardian.NativeSeatBinding(issuer_id=b'i'*32,actor_id=b'a'*32,credential_id=b'c'*32,
            actor_entity_id='identity:fixture',actor_session_instance=1,service_incarnation=b's'*32,
            authority_generation=1,native_server_id=identity.server_id,compositor_credential_id=self.credential.id,native_seat_id=seat)
        return guardian.SeatRegistration(operation_id=b'o'*32,binding=binding)
    def fence(self):
        return guardian.NativeFenceIntent(intent_id=b'f'*32,issuer_id=b'i'*32,target_kind=guardian.REVOCATION_ACTOR,target_id=b'a'*32)
    def test_distinct_roles_fence_stopped_owner(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian();self.assertEqual(len({worker.pid,owner.pid,issuer.pid,os.getpid()}),4)
        world.sendall(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,register_seat=self.registration(identity,seat))))
        reply=self.receive(world,guardian.GuardianReply);self.assertEqual(len(reply.registration_id),32)
        os.kill(owner.pid,signal.SIGSTOP)
        try:
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if any(line.startswith('State:') and line.split()[1]=='T' for line in Path(f'/proc/{owner.pid}/status').read_text().splitlines()):break
                time.sleep(.005)
            else:self.fail('Seat owner did not stop')
            issuer.stdin.write(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,fence=self.fence())));issuer.stdin.flush()
            reply=self.receive(issuer.stdout,guardian.GuardianReply);self.assertTrue(reply.HasField('remaining_fences'));self.assertEqual(reply.remaining_fences,0)
            self.assertEqual(worker.wait(timeout=5),0);self.assertEqual(issuer.wait(timeout=5),0)
        finally:os.kill(owner.pid,signal.SIGCONT)
        self.assertEqual(self.call(owner,2,key=ip.Key(seat=seat,keycode=38,pressed=True)).code,ip.FORBIDDEN)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_world_role_cannot_request_fence(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian()
        world.sendall(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,fence=self.fence())))
        self.assertEqual(worker.wait(timeout=5),2)
        self.assertIn('failed guardian service rejected reuse',(self.root/'guardian-roles.log').read_text())
        self.assertEqual(self.call(owner,2,status=ip.Seat(id=seat)).code,ip.OK)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_guardian_death_fences_registered_seat(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian()
        world.sendall(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,
            register_seat=self.registration(identity,seat))))
        self.assertEqual(len(self.receive(world,guardian.GuardianReply).registration_id),32)
        worker.kill();self.assertEqual(worker.wait(timeout=5),-signal.SIGKILL)
        sequence=1;deadline=time.monotonic()+2
        while True:
            sequence+=1
            reply=self.call(owner,sequence,key=ip.Key(seat=seat,keycode=38,pressed=True))
            if reply.code==ip.FORBIDDEN or time.monotonic()>=deadline:break
            self.assertEqual(reply.code,ip.NO_FOCUS,reply)
            time.sleep(.01)
        self.assertEqual(reply.code,ip.FORBIDDEN,'Guardian death left its registered seat usable')
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_same_process_cannot_own_both_roles(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian(same_sender=True,expect_ready=False)
        self.assertEqual(worker.wait(timeout=5),2)
        self.assertEqual(self.call(owner,2,status=ip.Seat(id=seat)).code,ip.OK)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_caught_service_failure_fences_seat_while_guardian_process_survives(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian(hold_after_failure=True)
        world.sendall(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,
            register_seat=self.registration(identity,seat))))
        self.assertEqual(len(self.receive(world,guardian.GuardianReply).registration_id),32)
        world.sendall(frame(guardian.GuardianRequest(protocol_version=1,request_id=2,fence=self.fence())))
        deadline=time.monotonic()+1.5
        while 'failed guardian service rejected reuse' not in (self.root/'guardian-roles.log').read_text():
            self.assertIsNone(worker.poll());self.assertLess(time.monotonic(),deadline);time.sleep(.005)
        sequence=1;deadline=time.monotonic()+1
        while True:
            self.assertIsNone(worker.poll(),'Process exit masked failure cleanup')
            sequence+=1;reply=self.call(owner,sequence,key=ip.Key(seat=seat,keycode=38,pressed=True))
            if reply.code==ip.FORBIDDEN or time.monotonic()>=deadline:break
            self.assertEqual(reply.code,ip.NO_FOCUS);time.sleep(.005)
        self.assertEqual(reply.code,ip.FORBIDDEN)
        self.assertIsNone(worker.poll(),'Process exit masked failure cleanup')
        worker.terminate();self.assertEqual(worker.wait(timeout=5),-signal.SIGTERM)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_issuer_role_cannot_register_seat(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian()
        registration=self.registration(identity,seat)
        world.sendall(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,register_seat=registration)))
        self.assertEqual(len(self.receive(world,guardian.GuardianReply).registration_id),32)
        issuer.stdin.write(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,register_seat=registration)));issuer.stdin.flush()
        self.assertEqual(worker.wait(timeout=5),2)
        self.assertEqual(issuer.wait(timeout=5),2)
        self.assertEqual(self.call(owner,2,status=ip.Seat(id=seat)).code,ip.OK)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_world_registration_with_wrong_incarnation_is_rejected(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian();registration=self.registration(identity,seat)
        registration.binding.service_incarnation=b'x'*32
        world.sendall(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,register_seat=registration)))
        self.assertEqual(worker.wait(timeout=5),2)
        self.assertEqual(self.call(owner,2,status=ip.Seat(id=seat)).code,ip.OK)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_replayed_world_request_is_rejected(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian();registration=self.registration(identity,seat)
        request=guardian.GuardianRequest(protocol_version=1,request_id=1,register_seat=registration)
        world.sendall(frame(request));self.assertEqual(len(self.receive(world,guardian.GuardianReply).registration_id),32)
        world.sendall(frame(request));self.assertEqual(worker.wait(timeout=5),2)
        self.assertEqual(self.call(owner,2,status=ip.Seat(id=seat)).code,ip.OK)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_inherited_world_channel_rejects_another_process(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian()
        request=guardian.GuardianRequest(protocol_version=1,request_id=1,register_seat=self.registration(identity,seat))
        child=os.fork()
        if child==0:
            try:world.sendall(frame(request));os._exit(0)
            except BaseException:os._exit(1)
        _,status=os.waitpid(child,0);self.assertEqual(os.waitstatus_to_exitcode(status),0)
        self.assertEqual(worker.wait(timeout=5),2)
        self.assertEqual(self.call(owner,2,status=ip.Seat(id=seat)).code,ip.OK)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_fence_with_wrong_issuer_is_rejected(self):
        owner=self.launch();self.read(owner,core.RenderResult);seat=self.call(owner,1,open=ip.Empty()).seat
        worker,world,issuer,identity=self.start_guardian();registration=self.registration(identity,seat)
        world.sendall(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,register_seat=registration)))
        self.assertEqual(len(self.receive(world,guardian.GuardianReply).registration_id),32)
        intent=self.fence();intent.issuer_id=b'x'*32
        issuer.stdin.write(frame(guardian.GuardianRequest(protocol_version=1,request_id=1,fence=intent)));issuer.stdin.flush()
        self.assertEqual(worker.wait(timeout=5),2);self.assertEqual(issuer.wait(timeout=5),2)
        self.assertEqual(self.call(owner,2,status=ip.Seat(id=seat)).code,ip.OK)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)

if __name__=='__main__':unittest.main(verbosity=2)
