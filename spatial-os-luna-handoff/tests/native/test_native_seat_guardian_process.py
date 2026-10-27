"""An independent prototype guardian fences a stopped native seat owner."""
import fcntl
import os
from pathlib import Path
import signal
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

class NativeSeatGuardianProcessTests(base.ScopedInputTests):
    def test_independent_guardian_fences_stopped_seat_owner(self):
        owner=self.launch();self.read(owner,core.RenderResult)
        opened=self.call(owner,1,open=ip.Empty());self.assertEqual(opened.code,ip.OK)
        identity=self.supervisor.inspect()
        value=config.NativeCompositorConfig(protocol_version=1,display_number=self.display,
            compositor_cookie=self.credential.cookie,server_id=identity.server_id,
            server_pid=self.server.pid,server_uid=os.getuid())
        fd=os.memfd_create('guardian-config',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        os.write(fd,value.SerializeToString())
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        log=(self.root/'guardian-worker.log').open('wb')
        try:worker=subprocess.Popen([WORKER,str(fd),str(self.root/'guardian.sqlite')],pass_fds=[fd],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log)
        finally:os.close(fd)
        def cleanup():
            if not worker.stdin.closed:worker.stdin.close()
            try:worker.wait(timeout=5)
            except subprocess.TimeoutExpired:worker.kill();worker.wait(timeout=5)
            worker.stdout.close();log.close()
            text=(self.root/'guardian-worker.log').read_text(errors='replace')
            for marker in ('AddressSanitizer','LeakSanitizer','ThreadSanitizer','runtime error:'):
                self.assertNotIn(marker,text)
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-guardian-worker.log')).write_bytes((self.root/'guardian-worker.log').read_bytes())
        self.addCleanup(cleanup)
        self.assertNotEqual(worker.pid,owner.pid)
        binding=guardian.NativeSeatBinding(issuer_id=b'i'*32,actor_id=b'a'*32,credential_id=b'c'*32,
            actor_entity_id='identity:fixture',actor_session_instance=1,service_incarnation=b's'*32,
            authority_generation=1,native_server_id=identity.server_id,compositor_credential_id=self.credential.id,
            native_seat_id=opened.seat)
        worker.stdin.write(frame(guardian.SeatRegistration(operation_id=b'o'*32,binding=binding)));worker.stdin.flush()
        self.assertTrue(self.read(worker,core.RenderResult).HasField('ready'))
        os.kill(owner.pid,signal.SIGSTOP)
        try:
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                status=Path(f'/proc/{owner.pid}/status').read_text().splitlines()
                if any(line.startswith('State:') and line.split()[1]=='T' for line in status):break
                time.sleep(.005)
            else:self.fail('Seat owner did not stop')
            intent=guardian.NativeFenceIntent(intent_id=b'f'*32,issuer_id=b'i'*32,
                target_kind=guardian.REVOCATION_ACTOR,target_id=b'a'*32)
            worker.stdin.write(frame(intent));worker.stdin.flush()
            self.assertTrue(self.read(worker,core.RenderResult).HasField('ready'))
            self.assertEqual(worker.wait(timeout=5),0)
        finally:os.kill(owner.pid,signal.SIGCONT)
        key=self.call(owner,2,key=ip.Key(seat=opened.seat,keycode=38,pressed=True))
        self.assertEqual(key.code,ip.FORBIDDEN)
        self.assertIsNone(owner.poll())
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)

if __name__=='__main__':unittest.main(verbosity=2)
