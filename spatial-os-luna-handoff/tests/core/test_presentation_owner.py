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
if mode.startswith('local_retire_'):
 if not r.HasField('select_local_actor'):raise RuntimeError('Missing initial selection')
 target=r.select_local_actor
 send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,
  local_actor_state=p.LocalActorPresentationState(target=target,selected=True)))
 r=receive()
 if not r.HasField('retire_local_actor') or r.retire_local_actor!=target:raise RuntimeError('Wrong exact retirement')
 time.sleep(.35)
 if mode=='local_retire_lost':s.shutdown(socket.SHUT_RDWR);s.close();time.sleep(10);sys.exit(0)
 reply=p.PresentationControlReply(protocol_version=1,request_id=r.request_id,
  local_actor_state=p.LocalActorPresentationState(target=target,selected=False))
 if mode=='local_retire_request':reply.request_id+=1
 if mode=='local_retire_target':reply.local_actor_state.target.identity.view_id=b'x'*32
 if mode=='local_retire_selected':reply.local_actor_state.selected=True
 if mode=='local_retire_denied':
  reply.ClearField('local_actor_state');reply.error.code=c.FORBIDDEN;reply.error.message='checked retirement denial'
 fd=os.memfd_create('bad-retirement') if mode=='local_retire_descriptor' else None
 send(reply,fd)
 if fd is not None:os.close(fd)
 if mode in ('local_retire_success','local_retire_controls','local_retire_capture_generation','local_retire_capture_active'):
  r=receive()
  if not r.HasField('controls'):raise RuntimeError('Missing exact hidden controls output')
  time.sleep(.1);reply=p.PresentationControlReply(protocol_version=1,request_id=r.request_id,controls=r.controls)
  if mode=='local_retire_controls':reply.controls.capture_generation+=1
  send(reply)
  if mode!='local_retire_controls':
   r=receive()
   if not r.HasField('stop_capture'):raise RuntimeError('Missing checked capture retirement')
   time.sleep(.1);reply=p.PresentationControlReply(protocol_version=1,request_id=r.request_id,
    capture=p.HostCaptureState(capture_generation=r.stop_capture.capture_generation,active=False))
   if mode=='local_retire_capture_generation':reply.capture.capture_generation+=1
   if mode=='local_retire_capture_active':reply.capture.active=True
   send(reply)
  if mode=='local_retire_success':
   r=receive();send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,shutting_down=True))
  else:receive()
 elif mode=='local_retire_denied':
  r=receive();send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,shutting_down=True))
 else:receive()
 sys.exit(0)
if mode.startswith('local_async_'):
 if not r.HasField('select_local_actor'):raise RuntimeError('Missing exact asynchronous selection')
 target=r.select_local_actor
 time.sleep(.35)
 reply=p.PresentationControlReply(protocol_version=1,request_id=r.request_id,
  local_actor_state=p.LocalActorPresentationState(target=target,selected=True))
 if mode=='local_async_request':reply.request_id+=1
 if mode=='local_async_target':reply.local_actor_state.target.identity.view_id=b'x'*32
 if mode=='local_async_denied':
  reply.ClearField('local_actor_state');reply.error.code=c.FORBIDDEN;reply.error.message='checked selection denial'
 send(reply)
 if mode in ('local_async_success','local_async_denied'):
  r=receive()
  if not r.HasField('retire_local_actor'):raise RuntimeError('Pending selection stole retirement')
  send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,
   local_actor_state=p.LocalActorPresentationState(target=target,selected=False)))
  r=receive();send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,shutting_down=True))
 else:receive()
 sys.exit(0)
if mode in ('lost_attach','lost_geometry_attach'):s.shutdown(socket.SHUT_RDWR);s.close();time.sleep(10);sys.exit(0)
if mode=='partial':s.sendall(b'\x00');time.sleep(10);sys.exit(0)
if mode=='missing_geometry_state':
 send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,shutting_down=True));receive();sys.exit(0)
if mode=='geometry_sequence':
 state=p.GeometryPresentationState(target=r.attach_geometry.target,stage=p.ATTACHED)
 send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,geometry_state=state))
 for operation in ('select_geometry','publish_geometry','select_geometry','retire_geometry'):
  r=receive()
  if not r.HasField(operation):raise RuntimeError('Unexpected geometry owner operation')
  state.stage=p.READY;state.selected=True
  if operation=='publish_geometry':state.last_view_sequence=r.publish_geometry.view.sequence
  if operation=='retire_geometry':state.stage=p.RETIRED;state.selected=False
  send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,geometry_state=state))
 r=receive();send(p.PresentationControlReply(protocol_version=1,request_id=r.request_id,shutting_down=True));sys.exit(0)

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
            if mode!='geometry_sequence':self.assertIn('Host presentation ownership unavailable',result.stdout)
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):
                self.assertNotIn(marker,result.stderr)

    def test_async_selection_keeps_poll_bounded_and_rejects_competing_owners(self):
        self.attempt('local_async_success','nonblocking polls confirmed')
    def test_async_selection_rejects_wrong_request_without_reusing_channel(self):
        self.attempt('local_async_request','reply identity mismatch')
    def test_async_selection_rejects_wrong_view_without_publishing_completion(self):
        self.attempt('local_async_target','Selected local actor view unconfirmed')
    def test_async_checked_denial_preserves_exact_retirement(self):
        self.attempt('local_async_denied','checked selection denial')

    def test_async_retirement_controls_and_capture_keep_polling_bounded(self):
        self.attempt('local_retire_success','retirement continuations confirmed')
    def test_async_retirement_rejects_wrong_request(self):
        self.attempt('local_retire_request','reply identity mismatch')
    def test_async_retirement_rejects_wrong_exact_target(self):
        self.attempt('local_retire_target','Retired local actor view unconfirmed')
    def test_async_retirement_cannot_accept_still_selected_receipt(self):
        self.attempt('local_retire_selected','Retired local actor view unconfirmed')
    def test_async_retirement_rejects_unsolicited_descriptors(self):
        self.attempt('local_retire_descriptor','Invalid host presentation reply')
    def test_async_retirement_transport_loss_keeps_uncertainty(self):
        self.attempt('local_retire_lost','Presentation input closed or truncated')
    def test_async_retirement_typed_denial_does_not_invent_clearance(self):
        self.attempt('local_retire_denied','checked retirement denial')
    def test_async_controls_requires_exact_generation_receipt(self):
        self.attempt('local_retire_controls','Native controls presentation unconfirmed')
    def test_async_capture_retirement_requires_exact_generation(self):
        self.attempt('local_retire_capture_generation','Native capture retirement unconfirmed')
    def test_async_capture_retirement_cannot_accept_active_receipt(self):
        self.attempt('local_retire_capture_active','Native capture retirement unconfirmed')

    def test_wrong_request_id(self):self.attempt('request_id','reply identity mismatch')
    def test_wrong_binding(self):self.attempt('binding','state mismatch')
    def test_wrong_attach_stage(self):self.attempt('attach_stage','state mismatch')
    def test_unrequested_selection(self):self.attempt('selected','state mismatch')
    def test_wrong_select_stage(self):self.attempt('select_stage','state mismatch')
    def test_unsolicited_descriptor(self):self.attempt('descriptor','Invalid host presentation reply')
    def test_lost_attach_reply(self):self.attempt('lost_attach','Presentation input closed or truncated')
    def test_partial_reply(self):self.attempt('partial','Presentation input deadline exceeded')
    def test_lost_geometry_attach_retains_attempted_cleanup_identity(self):
        self.attempt('lost_geometry_attach','Presentation input closed or truncated')
    def test_missing_geometry_reply_fences_owner_and_retains_cleanup_identity(self):
        self.attempt('missing_geometry_state','Missing geometry presentation state')
    def test_geometry_select_and_retire_preserve_published_sequence(self):
        self.attempt('geometry_sequence','geometry sequence confirmed')

if __name__=='__main__':unittest.main(verbosity=2)
