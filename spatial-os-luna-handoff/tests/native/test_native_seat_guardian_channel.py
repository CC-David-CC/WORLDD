"""A test guardian checks its peer while fencing a stopped X seat owner."""
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

class NativeSeatGuardianChannelTests(base.ScopedInputTests):
    def receive_channel(self,channel):
        def exact(size):
            data=b'';deadline=time.monotonic()+5
            while len(data)<size:
                remaining=deadline-time.monotonic()
                self.assertGreater(remaining,0,'Guardian reply timed out')
                self.assertTrue(select.select([channel],[],[],remaining)[0],'Guardian reply timed out')
                part=channel.recv(size-len(data));self.assertTrue(part,'Guardian closed its channel');data+=part
            return data
        size=struct.unpack('!I',exact(4))[0];self.assertLessEqual(size,1048576)
        return core.RenderResult.FromString(exact(size))
    def start_guardian(self):
        identity=self.supervisor.inspect()
        value=config.NativeCompositorConfig(protocol_version=1,display_number=self.display,
            compositor_cookie=self.credential.cookie,server_id=identity.server_id,
            server_pid=self.server.pid,server_uid=os.getuid())
        fd=os.memfd_create('guardian-config',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);os.write(fd,value.SerializeToString())
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        parent,child=socket.socketpair();log=(self.root/'guardian-channel.log').open('wb')
        try:
            worker=subprocess.Popen([WORKER,str(fd),str(child.fileno()),str(self.root/'guardian.sqlite'),str(os.getpid()),str(os.getpid())],
                pass_fds=[fd,child.fileno()],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log)
        finally:os.close(fd);child.close()
        def cleanup():
            parent.close()
            try:worker.wait(timeout=5)
            except subprocess.TimeoutExpired:worker.kill();worker.wait(timeout=5)
            log.close();text=(self.root/'guardian-channel.log').read_text(errors='replace')
            for marker in ('AddressSanitizer','LeakSanitizer','ThreadSanitizer','runtime error:'):self.assertNotIn(marker,text)
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-guardian-channel.log')).write_text(text)
        self.addCleanup(cleanup)
        ready=self.receive_channel(parent);self.assertEqual(ready.request_id,0);self.assertTrue(ready.HasField('ready'))
        return worker,parent,identity
    def binding(self,identity,seat):
        return guardian.NativeSeatBinding(issuer_id=b'i'*32,actor_id=b'a'*32,credential_id=b'c'*32,
            actor_entity_id='identity:fixture',actor_session_instance=1,service_incarnation=b's'*32,
            authority_generation=1,native_server_id=identity.server_id,compositor_credential_id=self.credential.id,native_seat_id=seat)
    def test_checked_channel_fences_stopped_owner(self):
        owner=self.launch();self.read(owner,core.RenderResult)
        opened=self.call(owner,1,open=ip.Empty());self.assertEqual(opened.code,ip.OK)
        worker,channel,identity=self.start_guardian();self.assertNotEqual(worker.pid,owner.pid)
        channel.sendall(frame(guardian.SeatRegistration(operation_id=b'o'*32,binding=self.binding(identity,opened.seat))))
        self.assertEqual(self.receive_channel(channel).request_id,1)
        os.kill(owner.pid,signal.SIGSTOP)
        try:
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                status=Path(f'/proc/{owner.pid}/status').read_text().splitlines()
                if any(line.startswith('State:') and line.split()[1]=='T' for line in status):break
                time.sleep(.005)
            else:self.fail('Seat owner did not stop')
            intent=guardian.NativeFenceIntent(intent_id=b'f'*32,issuer_id=b'i'*32,target_kind=guardian.REVOCATION_ACTOR,target_id=b'a'*32)
            channel.sendall(frame(intent));self.assertEqual(self.receive_channel(channel).request_id,2)
            self.assertEqual(worker.wait(timeout=5),0)
        finally:os.kill(owner.pid,signal.SIGCONT)
        self.assertEqual(self.call(owner,2,key=ip.Key(seat=opened.seat,keycode=38,pressed=True)).code,ip.FORBIDDEN)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)
    def test_different_process_cannot_send_on_inherited_channel(self):
        owner=self.launch();self.read(owner,core.RenderResult)
        opened=self.call(owner,1,open=ip.Empty());self.assertEqual(opened.code,ip.OK)
        worker,channel,identity=self.start_guardian()
        payload=frame(guardian.SeatRegistration(operation_id=b'o'*32,binding=self.binding(identity,opened.seat)))
        child=os.fork()
        if child==0:
            try:channel.sendall(payload);os._exit(0)
            except BaseException:os._exit(1)
        _,status=os.waitpid(child,0);self.assertEqual(os.waitstatus_to_exitcode(status),0)
        self.assertEqual(worker.wait(timeout=5),2)
        self.assertEqual(self.call(owner,2,status=ip.Seat(id=opened.seat)).code,ip.OK)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)

if __name__=='__main__':unittest.main(verbosity=2)
