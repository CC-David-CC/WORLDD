"""Production actor cleanup ownership against independent failing compositor peers."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import core_pb2 as core

PROBE=sys.argv.pop(1)
PEER=r'''#!/usr/bin/python3
import os,struct,sys,time
from pathlib import Path
import core_pb2 as core
root=Path(sys.argv[sys.argv.index('--state-dir')+1]);scenario=os.environ['STOP_OWNER_SCENARIO']
def send(message):
 data=message.SerializeToString();sys.stdout.buffer.write(struct.pack('!I',len(data))+data);sys.stdout.buffer.flush()
def wait(name):
 deadline=time.monotonic()+5
 while not (root/name).exists():
  if time.monotonic()>deadline:raise RuntimeError('Peer fixture signal deadline')
  time.sleep(.001)
send(core.RenderResult(protocol_version=1,ready=core.RendererReady()))
count=0
while True:
 header=sys.stdin.buffer.read(4)
 if not header:break
 size=struct.unpack('!I',header)[0];command=core.RenderCommand.FromString(sys.stdin.buffer.read(size));count+=1
 (root/('command-'+str(count)+'.pb')).write_bytes(command.SerializeToString())
 result=core.RenderResult(protocol_version=1,request_id=command.request_id)
 if command.HasField('sync'):
  result.snapshot.actors.add(actor_id='actor-one')
 elif command.input.HasField('cancel'):
  (root/'pending-cancel').write_text('1');wait('release-cancel')
  if scenario=='cancel-denied':result.error.code=core.FORBIDDEN;result.error.message='fixture Cancel denial'
  else:result.input.actor_id=command.input.actor_id
 elif command.input.HasField('close'):
  (root/'pending-close').write_text('1');wait('release-close')
  if scenario=='close-lost':sys.exit(0)
  if scenario=='close-denied':result.error.code=core.FORBIDDEN;result.error.message='fixture Close denial'
  else:result.input.actor_id=command.input.actor_id
 else:raise RuntimeError('Unexpected native command')
 send(result)
'''


class ActorStopOwnerTests(unittest.TestCase):
    def checked(self,scenario):
        with tempfile.TemporaryDirectory(prefix='spatial-stop-owner-') as temp:
            root=Path(temp);peer=root/'peer.py';peer.write_text(PEER);peer.chmod(0o700)
            result=subprocess.run([PROBE,str(peer),str(root),scenario],capture_output=True,timeout=15,
                env={**os.environ,'STOP_OWNER_SCENARIO':scenario})
            self.assertEqual(result.returncode,0,result.stderr.decode())
            self.assertEqual(result.stdout.decode(),'PASS '+scenario+'\n')
            for marker in ['AddressSanitizer','LeakSanitizer','runtime error:']:self.assertNotIn(marker,result.stderr.decode())
            commands=[core.RenderCommand.FromString(p.read_bytes()) for p in sorted((root/'renderer').glob('command-*.pb'))]
            self.assertEqual(len(commands),3,'Cancellation was replayed or native ownership was lost')
            self.assertEqual([item.request_id for item in commands],[1,2,3])
            self.assertTrue(commands[0].HasField('sync'))
            self.assertEqual([item.input.WhichOneof('operation') for item in commands[1:]],['cancel','close'])
            self.assertEqual([item.input.actor_id for item in commands[1:]],['actor-one','actor-one'])

    def test_checked_close_gates_new_actor_incarnation(self):self.checked('checked-close')
    def test_abandoned_stop_retains_cleanup_until_checked_close(self):self.checked('abandoned')
    def test_typed_close_denial_retains_barrier_without_retry(self):self.checked('close-denied')
    def test_lost_close_transport_retains_native_owner_without_replay(self):self.checked('close-lost')
    def test_typed_cancel_denial_can_upgrade_to_one_distinct_close(self):self.checked('cancel-denied')


if __name__=='__main__':unittest.main(verbosity=2)
