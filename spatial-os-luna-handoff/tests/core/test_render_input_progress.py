"""Nonblocking cancellation ownership against independent protobuf peers."""
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import core_pb2 as core
from harness.wire import frame

PROBE=sys.argv.pop(1)
PEER=r'''#!/usr/bin/python3
import os,socket,struct,sys,time
from pathlib import Path
import core_pb2 as core
def send(value):
 data=value.SerializeToString();sys.stdout.buffer.write(struct.pack('!I',len(data))+data);sys.stdout.buffer.flush()
send(core.RenderResult(protocol_version=1,request_id=0,ready=core.RendererReady()))
fault=os.environ.get('INPUT_PROGRESS_FAULT','')
root=Path(sys.argv[sys.argv.index('--state-dir')+1]);count=0
while True:
 header=sys.stdin.buffer.read(4)
 if not header:break
 size=struct.unpack('!I',header)[0];command=core.RenderCommand.FromString(sys.stdin.buffer.read(size));count+=1
 (root/('command-'+str(count)+'.pb')).write_bytes(command.SerializeToString())
 if count==1:
  if fault=='loss':sys.exit(0)
  if fault=='delay':time.sleep(.4)
 result=core.RenderResult(protocol_version=1,request_id=command.request_id)
 result.input.actor_id=command.input.actor_id
 if count==1:
  if fault=='identity':result.input.actor_id='another-actor'
  elif fault=='request':result.request_id+=1
  elif fault=='version':result.protocol_version=2
  elif fault=='keys':result.input.held_keys.append('Shift_L')
  elif fault=='buttons':result.input.held_buttons.append(1)
  elif fault=='focus':result.input.focus.id='surface';result.input.focus.generation=1
  elif fault=='unknown':result.input.MergeFromString(b'\xa0\x06\x01')
  elif fault=='denied':result.ClearField('input');result.error.code=core.FORBIDDEN;result.error.message='denied'
 data=result.SerializeToString();packet=struct.pack('!I',len(data))+data
 if count==1 and fault=='malformed':packet=struct.pack('!I',1)+b'\xff'
 if count==1 and fault=='oversize':packet=struct.pack('!I',0x7fffffff)
 if count==1 and fault=='truncated':sys.stdout.buffer.write(packet[:-1]);sys.stdout.buffer.flush();sys.exit(0)
 if count==1 and fault=='descriptor':
  import array
  fd=os.open('/dev/null',os.O_RDONLY)
  socket.socket(fileno=os.dup(0)).sendmsg([packet],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))]);os.close(fd)
 elif count==1 and fault=='drip':
  for byte in packet:
   sys.stdout.buffer.write(bytes([byte]));sys.stdout.buffer.flush();time.sleep(3)
 else:sys.stdout.buffer.write(packet);sys.stdout.buffer.flush()
'''


class RenderInputProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='spatial-input-progress-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.peer=self.root/'peer.py';self.peer.write_text(PEER);self.peer.chmod(0o700)

    def call(self,fault='',close=False,invalid=False):
        request=core.RenderCommand(protocol_version=1,request_id=7)
        request.input.actor_id='actor-one'
        if invalid:request.input.key.keysym='q'
        elif close:request.input.close.SetInParent()
        else:request.input.cancel.SetInParent()
        started=time.monotonic()
        argv=[PROBE,str(self.peer),str(self.root)]
        process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env={**os.environ,'INPUT_PROGRESS_FAULT':fault},bufsize=0,start_new_session=True)
        self.response_elapsed=None
        def read_exact(size,allow_eof=False):
            data=b''
            while len(data)<size:
                remaining=started+14-time.monotonic()
                self.assertGreater(remaining,0,'Probe response deadline exceeded')
                ready,_,_=select.select([process.stdout],[],[],remaining)
                self.assertTrue(ready,'Probe response deadline exceeded')
                part=os.read(process.stdout.fileno(),size-len(data))
                if not part and allow_eof and not data:return b''
                self.assertTrue(part,'Truncated probe response')
                data+=part
            return data
        try:
            process.stdin.write(frame(request));process.stdin.close();process.stdin=None
            header=read_exact(4,allow_eof=invalid)
            packet=b''
            if header:
                size=struct.unpack('!I',header)[0];self.assertLessEqual(size,1024*1024)
                packet=header+read_exact(size);self.response_elapsed=time.monotonic()-started
            stdout,stderr=process.communicate(timeout=max(.001,started+14-time.monotonic()))
            self.elapsed=time.monotonic()-started
            return subprocess.CompletedProcess(argv,process.returncode,packet+stdout,stderr)
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL);process.wait()
            for stream in [process.stdin,process.stdout,process.stderr]:
                if stream is not None:stream.close()

    def accepted(self,result):
        self.assertEqual(result.returncode,0,result.stderr.decode());self.assertGreaterEqual(len(result.stdout),4)
        size=struct.unpack('!I',result.stdout[:4])[0];self.assertEqual(size,len(result.stdout)-4)
        reply=core.RenderResult.FromString(result.stdout[4:]);self.assertEqual((reply.protocol_version,reply.request_id),(1,7))
        self.assertEqual(reply.input.actor_id,'actor-one');self.assertFalse(reply.input.held_keys);self.assertFalse(reply.input.held_buttons)
        self.assertFalse(reply.input.HasField('focus'))
        commands=[core.RenderCommand.FromString(p.read_bytes()) for p in sorted(self.root.glob('command-*.pb'))]
        self.assertEqual(len(commands),2);self.assertEqual([v.request_id for v in commands],[1,2])

    def rejected(self,fault,marker):
        result=self.call(fault);self.assertEqual(result.returncode,2,result.stderr.decode());self.assertGreaterEqual(len(result.stdout),4)
        size=struct.unpack('!I',result.stdout[:4])[0];self.assertEqual(size,len(result.stdout)-4)
        reply=core.RenderResult.FromString(result.stdout[4:]);self.assertEqual((reply.protocol_version,reply.request_id),(1,7))
        self.assertEqual(reply.WhichOneof('outcome'),'error')
        self.assertEqual(reply.error.code,core.FORBIDDEN if fault=='denied' else core.UNAVAILABLE)
        if isinstance(marker,tuple):self.assertTrue(any(value in reply.error.message for value in marker),reply.error.message)
        else:self.assertIn(marker,reply.error.message)
        self.assertIn(reply.error.message,result.stderr.decode())
        for text in ['AddressSanitizer','LeakSanitizer','runtime error:']:self.assertNotIn(text,result.stderr.decode())
        self.assertLessEqual(len(list(self.root.glob('command-*.pb'))),1)

    def test_delayed_reply_keeps_polls_bounded_and_exact_owner(self):
        self.accepted(self.call('delay'));self.assertGreaterEqual(self.elapsed,.4)
    def test_close_uses_same_checked_continuation(self):self.accepted(self.call(close=True))
    def test_non_cancellation_cannot_enter_priority_stream(self):
        result=self.call(invalid=True);self.assertEqual(result.returncode,2);self.assertFalse(result.stdout)
        self.assertIn(b'requires exact actor cancellation',result.stderr);self.assertFalse(list(self.root.glob('command-*.pb')))
    def test_foreign_actor_reply_is_rejected(self):self.rejected('identity','not confirmed')
    def test_wrong_request_is_rejected(self):self.rejected('request','identity mismatch')
    def test_wrong_version_is_rejected(self):self.rejected('version','identity mismatch')
    def test_held_key_is_not_a_cancellation_receipt(self):self.rejected('keys','not confirmed')
    def test_held_button_is_not_a_cancellation_receipt(self):self.rejected('buttons','not confirmed')
    def test_focus_is_not_a_cancellation_receipt(self):self.rejected('focus','not confirmed')
    def test_unknown_fields_are_rejected(self):self.rejected('unknown','Unknown protobuf field')
    def test_correlated_denial_never_claims_cancelled(self):self.rejected('denied','denied')
    def test_malformed_frame_is_rejected(self):self.rejected('malformed','identity mismatch')
    def test_oversize_frame_is_rejected(self):self.rejected('oversize','Excessive native cancellation reply')
    def test_transport_loss_retains_uncertainty(self):self.rejected('loss',('Compositor exited','response was truncated'))
    def test_truncated_reply_retains_uncertainty(self):self.rejected('truncated',('Compositor exited','response was truncated'))
    def test_descriptors_are_rejected(self):self.rejected('descriptor','ancillary data')
    def test_fragments_do_not_restart_absolute_deadline(self):
        self.rejected('drip','deadline exceeded')
        self.assertGreaterEqual(self.response_elapsed,10);self.assertLess(self.response_elapsed,11)
        self.assertLess(self.elapsed-self.response_elapsed,3)


if __name__=='__main__':unittest.main(verbosity=2)
