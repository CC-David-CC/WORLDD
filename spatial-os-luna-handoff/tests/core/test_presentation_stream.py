"""Kernel-authenticated protobuf frames and bounded descriptor transfer."""
import array
import fcntl
import os
from pathlib import Path
import re
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import core_pb2 as core
from harness.wire import frame,read_message
PROBE=Path(sys.argv.pop(1)).resolve()
class PresentationStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def start(self,creator=None):
        self.peer,child=socket.socketpair();self.peer.settimeout(2)
        log=self.root/'stream.log';self.log=log.open('wb')
        self.proc=subprocess.Popen([str(PROBE),str(child.fileno()),str(creator or os.getpid()),str(os.getpid())],
            pass_fds=(child.fileno(),),stdout=subprocess.PIPE,stderr=self.log)
        child.close()
        def cleanup():
            self.peer.close()
            try:self.proc.wait(timeout=7)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3);self.fail('Presentation stream probe hung')
            self.proc.stdout.close();self.log.close();text=log.read_text(errors='replace')
            self.assertEqual(self.proc.returncode,2,text)
            counts=re.search(r'descriptors_before=(\d+) descriptors_after=(\d+)',text);self.assertIsNotNone(counts,text)
            self.assertEqual(int(counts[1])-1,int(counts[2]),text)
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,text)
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-stream.log')).write_text(text)
        self.addCleanup(cleanup)
        if creator is None:
            self.assertTrue(select.select([self.proc.stdout],[],[],5)[0])
            self.assertTrue(read_message(self.proc.stdout,core.RenderResult).HasField('ready'))
    def payload(self,sequence=1):return core.Request(protocol_version=1,request_id=sequence,hello=core.HelloRequest()).SerializeToString()
    def receive(self):
        first,ancillary,flags,_=self.peer.recvmsg(1,socket.CMSG_SPACE(8*4))
        self.assertEqual(len(first),1);self.assertFalse(flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC))
        descriptors=[]
        for level,kind,data in ancillary:
            self.assertEqual((level,kind),(socket.SOL_SOCKET,socket.SCM_RIGHTS))
            values=array.array('i');values.frombytes(data);descriptors.extend(values)
        def exact(n):
            out=b''
            while len(out)<n:
                part=self.peer.recv(n-len(out));self.assertTrue(part);out+=part
            return out
        size=struct.unpack('!I',first+exact(3))[0];self.assertLessEqual(size,1048576)
        return exact(size),descriptors
    def send(self,payload,descriptors=()):
        data=struct.pack('!I',len(payload))+payload
        ancillary=[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',descriptors))] if descriptors else []
        sent=self.peer.sendmsg([data],ancillary);self.assertGreater(sent,0)
        self.peer.sendall(data[sent:])
    def failure(self,text,timeout=2):
        self.assertEqual(self.proc.wait(timeout=timeout),2)
        self.log.flush();self.assertIn(text,(self.root/'stream.log').read_text())
    def test_protobuf_frames_remain_distinct(self):
        self.start()
        for sequence in range(1,25):
            data=self.payload(sequence);self.send(data);reply,files=self.receive()
            self.assertEqual(reply,data);self.assertEqual(files,[])
    def test_two_sealed_descriptors_round_trip_without_leaks(self):
        self.start();owned=[]
        try:
            for content in (b'world-a',b'world-b'):
                fd=os.memfd_create('fixture',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);owned.append(fd);os.write(fd,content)
                fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
            for sequence in range(1,20):
                self.send(self.payload(sequence),owned);reply,files=self.receive()
                try:
                    self.assertEqual(reply,self.payload(sequence));self.assertEqual(len(files),2)
                    self.assertEqual([os.pread(fd,20,0) for fd in files],[b'world-a',b'world-b'])
                    self.assertTrue(all(fcntl.fcntl(fd,fcntl.F_GET_SEALS)&fcntl.F_SEAL_WRITE for fd in files))
                finally:
                    for fd in files:os.close(fd)
        finally:
            for fd in owned:os.close(fd)
    def test_three_descriptors_are_rejected(self):
        self.start();fd=os.open('/dev/null',os.O_RDONLY)
        try:self.send(self.payload(),[fd]*3)
        finally:os.close(fd)
        self.failure('Unexpected presentation descriptors')
    def test_truncated_ancillary_data_is_rejected_without_descriptor_leaks(self):
        self.start();fd=os.open('/dev/null',os.O_RDONLY)
        try:self.send(self.payload(),[fd]*24)
        finally:os.close(fd)
        self.failure('Presentation message sender mismatch')
    def test_descriptor_after_first_frame_byte_is_rejected(self):
        self.start();data=struct.pack('!I',len(self.payload()))+self.payload();self.peer.sendall(data[:1])
        fd=os.open('/dev/null',os.O_RDONLY)
        try:self.peer.sendmsg([data[1:]],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))])
        finally:os.close(fd)
        self.failure('Unexpected presentation descriptors')
    def test_inherited_socket_does_not_authorize_another_sender(self):
        self.start();child=os.fork()
        if child==0:
            try:self.send(self.payload())
            finally:os._exit(0)
        os.waitpid(child,0);self.failure('Presentation message sender mismatch')
    def test_wrong_socket_creator_is_rejected(self):
        self.start(creator=os.getpid()+100000);self.failure('Presentation socket creator mismatch')
    def test_excessive_frame_is_rejected(self):
        self.start();self.peer.sendall(struct.pack('!I',1048577));self.failure('Excessive presentation frame')
    def test_partial_progress_does_not_refresh_five_second_deadline(self):
        self.start();started=time.monotonic();self.peer.sendall(b'\x00');time.sleep(3);self.peer.sendall(b'\x00')
        self.failure('Presentation input deadline exceeded',timeout=4)
        elapsed=time.monotonic()-started;self.assertGreaterEqual(elapsed,5);self.assertLess(elapsed,6.5)
    def test_stalled_reader_hits_fixed_output_deadline(self):
        self.start();self.peer.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096)
        data=core.Request(protocol_version=1,request_id=1,
            world_create=core.WorldCreateRequest(entity=core.Entity(label='x'*500000))).SerializeToString()
        started=time.monotonic();self.send(data)
        self.failure('Presentation output deadline exceeded',timeout=7)
        elapsed=time.monotonic()-started;self.assertGreaterEqual(elapsed,5);self.assertLess(elapsed,6.5)

    def test_truncated_payload_is_rejected(self):
        self.start();self.peer.sendall(struct.pack('!I',20)+b'x');self.peer.shutdown(socket.SHUT_WR)
        self.failure('Presentation input closed or truncated')
if __name__=='__main__':unittest.main(verbosity=2)
