import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests/core'))
import os,signal,select,time,struct,unittest
import test_world_application_control as control
from harness.wire import frame
from test_world_identity import receive
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session

class CancellationCloseTests(control.WorldApplicationControlTests):
    def native_device_count(self):
        root=self.native.supervisor;name=b'XInputExtension'
        head,_=root.request(98,struct.pack('<HH',len(name),0)+name)
        self.assertTrue(head[8]);opcode=head[9]
        root.request(opcode,struct.pack('<HH',2,0),minor=47)
        head,_=root.request(opcode,struct.pack('<HH',0,0),minor=48)
        return struct.unpack_from('<H',head,8)[0]

    def test_revocation_during_native_cancel_closes_actor_devices(self):
        app,surface,observe,lease=self.begin_control();self.send_key_event('a',True)
        before=self.native_device_count()
        children=Path(f'/proc/{self.world_process.proc.pid}/task/{self.world_process.proc.pid}/children').read_text().split()
        compositor=next(int(pid) for pid in children if Path(f'/proc/{pid}/comm').read_text().strip()=='spatialwm')
        os.kill(compositor,signal.SIGSTOP)
        try:
            self.viewer.sequence+=1;self.viewer.action_sequence+=1
            request=session.WorldSessionRequest(protocol_version=1,request_id=self.viewer.sequence,
                action=core.ActorActionRequest(seq=self.viewer.action_sequence,unfocus=core.ActorStopRequest()))
            self.viewer.stream.sendall(frame(request))
            self.assertFalse(select.select([self.viewer.stream],[],[],.1)[0])
            self.revoke_viewer_identity(issuer.CREDENTIAL_TARGET)
            time.sleep(.2)
        finally:os.kill(compositor,signal.SIGCONT)
        receive(self.viewer.stream.recv,self.viewer.stream,session.WorldSessionReply)
        self.assert_released(app,inspect=False)
        self.until(self.native_device_count,lambda count:count==before-4)

if __name__=='__main__':
    unittest.main(defaultTest='CancellationCloseTests.test_revocation_during_native_cancel_closes_actor_devices',verbosity=2)
