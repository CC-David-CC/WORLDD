"""The world client receives an exact receipt before guardian lifetime fencing."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys

PROBE=str(Path(sys.argv.pop(3)).resolve())
import test_scoped_input as base
import compositor_native_pb2 as compositor
import core_pb2 as core
import input_pb2 as native_input


class NativeSeatGuardianWorldTests(base.ScopedInputTests):
    def test_checked_registration_receipt_precedes_input_and_disconnect_revokes(self):
        owner=self.launch();self.read(owner,core.RenderResult)
        seat=self.call(owner,1,open=native_input.Empty()).seat
        identity=self.supervisor.inspect()
        config=compositor.NativeCompositorConfig(protocol_version=1,display_number=self.display,
            compositor_cookie=self.credential.cookie,server_id=identity.server_id,
            server_pid=self.server.pid,server_uid=os.getuid())
        descriptor=os.memfd_create('native-guardian-world-config',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
        os.write(descriptor,config.SerializeToString())
        fcntl.fcntl(descriptor,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        try:
            result=subprocess.run([PROBE,str(descriptor),str(self.root/'native-seat-ledger.db'),identity.server_id,
                self.credential.id,str(seat)],pass_fds=[descriptor],capture_output=True,text=True,timeout=10)
        finally:os.close(descriptor)
        self.assertEqual(result.returncode,0,result.stderr)
        denied=self.call(owner,2,key=native_input.Key(seat=seat,keycode=38,pressed=True))
        self.assertEqual(denied.code,native_input.FORBIDDEN,denied)
        owner.stdin.close();self.assertEqual(owner.wait(timeout=5),0)


if __name__=='__main__':
    import unittest
    unittest.main(verbosity=2)
