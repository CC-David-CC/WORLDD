"""Hostile child replies exercise the production host presentation client."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import compositor_native_pb2 as native

PROBE=sys.argv.pop(1)
PEER=r'''#!/usr/bin/python3
import array,os,socket,struct,sys,time
import presentation_pb2 as p
import core_pb2 as c
s=socket.socket(fileno=0)
mode=os.environ['OWNER_TEST_MODE']
def send(v,fd=None):
 data=v.SerializeToString();data=struct.pack('!I',len(data))+data
 sent=s.sendmsg([data],[] if fd is None else [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))]);s.sendall(data[sent:])
def exact(n):
 data=b''
 while len(data)<n:
  part=s.recv(n-len(data))
  if not part:sys.exit(0)
  data+=part
 return data
def receive():return p.PresentationControlRequest.FromString(exact(struct.unpack('!I',exact(4))[0]))
send(p.PresentationControlReply(protocol_version=1,ready=c.RendererReady()))
r=receive()
if mode=='lost_attach':s.shutdown(socket.SHUT_RDWR);s.close();time.sleep(10);sys.exit(0)
if mode=='partial':s.sendall(b'\x00');time.sleep(10);sys.exit(0)
state=p.PresentationState(binding=r.attach.binding,stage=p.ATTACHED)
if mode=='binding':state.binding.world_id='foreign'
if mode=='attach_stage':state.stage=p.RETIRED
if mode=='selected':state.selected=True
reply=p.PresentationControlReply(protocol_version=1,request_id=r.request_id+(mode=='request_id'),state=state)
fd=os.memfd_create('bad-reply') if mode=='descriptor' else None
send(reply,fd)
if fd is not None:os.close(fd)
if mode=='select_stage':
 r=receive();state.stage=p.ATTACHED;state.selected=True
 send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,state=state))
receive()
'''

class PresentationOwnerTests(unittest.TestCase):
    def attempt(self,mode,diagnostic):
        with tempfile.TemporaryDirectory(prefix='presentation-owner-') as directory:
            root=Path(directory);peer=root/'peer';peer.write_text(PEER);peer.chmod(0o700)
            config=native.NativeCompositorConfig(protocol_version=1,display_number=99,
                compositor_cookie=b'c'*32,server_id=b's'*32,server_pid=os.getpid(),server_uid=os.getuid())
            fd=os.memfd_create('owner-test-config',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
            try:
                os.write(fd,config.SerializeToString())
                fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
                result=subprocess.run([PROBE,str(peer),str(root),str(fd),mode],pass_fds=(fd,),
                    env=dict(os.environ,OWNER_TEST_MODE=mode),capture_output=True,text=True,timeout=15)
            finally:os.close(fd)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn(diagnostic,result.stdout)
            self.assertIn('Host presentation ownership unavailable',result.stdout)
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):
                self.assertNotIn(marker,result.stderr)

    def test_wrong_request_id(self):self.attempt('request_id','reply identity mismatch')
    def test_wrong_binding(self):self.attempt('binding','state mismatch')
    def test_wrong_attach_stage(self):self.attempt('attach_stage','state mismatch')
    def test_unrequested_selection(self):self.attempt('selected','state mismatch')
    def test_wrong_select_stage(self):self.attempt('select_stage','state mismatch')
    def test_unsolicited_descriptor(self):self.attempt('descriptor','Invalid host presentation reply')
    def test_lost_attach_reply(self):self.attempt('lost_attach','Presentation input closed or truncated')
    def test_partial_reply(self):self.attempt('partial','Presentation input deadline exceeded')

if __name__=='__main__':unittest.main(verbosity=2)
