"""Exercise the actual M09 relay over sockets without native fixture imports."""
import ast
from pathlib import Path
import select
import socket
import struct
import threading
import time
from types import SimpleNamespace
import unittest
import broker_pb2 as pb
from harness.wire import frame

SOURCE=Path(__file__).with_name('test_application_observation.py')
node=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='BrokerRelay')
namespace=dict(socket=socket,select=select,struct=struct,threading=threading,time=time,pb=pb,frame=frame)
exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),namespace)
BrokerRelay=namespace['BrokerRelay']

class BrokerRelayWireTests(unittest.TestCase):
    def setUp(self):
        broker, self.peer=socket.socketpair();self.peer.settimeout(2)
        self.addCleanup(self.peer.close);self.addCleanup(broker.close)
        self.relay=BrokerRelay(SimpleNamespace(stream=broker,proc=None))
        self.relay.stream.settimeout(2);self.addCleanup(self.relay.close)
    def lease(self):
        result=pb.ApplicationControlLease(id=b'l'*32)
        result.principal.issuer_id=b'i'*32;result.principal.actor_id=b'a'*32
        result.application.workspace=b'w';result.application.application=b'p';result.application.server_id=b's'*32
        return result
    def notice(self,identifier=b'l'*32):
        result=pb.Reply(protocol_version=1,request_id=0);result.control_revoked.CopyFrom(self.lease())
        result.control_revoked.id=identifier;return result
    def request(self,acquire=False):
        result=pb.Request(protocol_version=1,request_id=41)
        if acquire:
            result.control_acquire.principal.CopyFrom(self.lease().principal)
            result.control_acquire.application.CopyFrom(self.lease().application)
        else:result.application_access.principal.CopyFrom(self.lease().principal)
        self.relay.stream.sendall(frame(result))
        received=self.relay.read(self.peer,pb.Request);self.assertEqual(received,result)
        return result
    def receive(self):return self.relay.read(self.relay.stream,pb.Reply)
    def test_idle_notices_are_forwarded_without_a_request(self):
        self.peer.sendall(frame(self.notice()))
        self.assertEqual(self.receive(),self.notice());self.assertEqual(self.relay.calls,0)
    def test_multiple_notices_do_not_consume_the_pending_response(self):
        self.request();one=self.notice();two=self.notice(b'm'*32)
        reply=pb.Reply(protocol_version=1,request_id=41);reply.application_access.SetInParent()
        self.peer.sendall(frame(one)+frame(two)+frame(reply))
        self.assertEqual(self.receive(),one);self.assertEqual(self.receive(),two);self.assertEqual(self.receive(),reply)
        self.assertEqual(self.relay.calls,1)
    def test_notice_does_not_consume_control_acquire_barrier(self):
        self.relay.hold_next_control_acquire();self.request(acquire=True)
        notice=self.notice();self.peer.sendall(frame(notice));self.assertEqual(self.receive(),notice)
        self.assertFalse(self.relay.control_acquire_reply_seen.is_set())
        reply=pb.Reply(protocol_version=1,request_id=41);reply.control_lease.CopyFrom(self.lease())
        self.peer.sendall(frame(reply))
        self.assertTrue(self.relay.control_acquire_reply_seen.wait(2))
        self.assertTrue(self.relay.control_acquire_entered.is_set())
        self.assertTrue(self.relay.control_acquire_record()['correlated_success'])
        self.assertFalse(select.select([self.relay.stream],[],[],0)[0])
        self.relay.release_control_acquire();self.assertEqual(self.receive(),reply)
    def test_access_hook_only_sees_the_correlated_access_response(self):
        rows=[];self.relay.hook=lambda count,reply:rows.append((count,reply.request_id))
        self.request();reply=pb.Reply(protocol_version=1,request_id=41);reply.application_access.SetInParent()
        self.peer.sendall(frame(self.notice())+frame(reply));self.receive();self.receive()
        self.assertEqual(rows,[(1,41)])
    def test_wrong_correlated_response_is_rejected(self):
        self.request();reply=pb.Reply(protocol_version=1,request_id=42);reply.application_access.SetInParent()
        self.peer.sendall(frame(reply));self.relay.thread.join(2)
        self.assertFalse(self.relay.thread.is_alive());self.assertEqual(len(self.relay.errors),1)
        self.assertIn('Mismatched relay response identity',str(self.relay.errors.pop()))
    def test_malformed_notice_identity_is_rejected(self):
        notice=self.notice();notice.request_id=7;self.peer.sendall(frame(notice));self.relay.thread.join(2)
        self.assertFalse(self.relay.thread.is_alive());self.assertEqual(len(self.relay.errors),1)
        self.assertIn('Invalid relay revocation identity',str(self.relay.errors.pop()))

    def test_world_eof_during_pending_request_retires_without_protocol_error(self):
        self.request();self.relay.stream.shutdown(socket.SHUT_WR)
        self.relay.thread.join(2)
        self.assertFalse(self.relay.thread.is_alive());self.assertFalse(self.relay.errors)
    def test_close_cancels_a_partial_broker_frame(self):
        entered=threading.Event();original=self.relay.read
        def read(stream,message,timeout=10):
            if stream is self.relay.broker.stream:entered.set()
            return original(stream,message,timeout)
        self.relay.read=read;self.request()
        self.peer.sendall(struct.pack('!I',8)+b'xx')
        self.assertTrue(entered.wait(2));started=time.monotonic();self.relay.close()
        self.assertLess(time.monotonic()-started,1)
        self.assertFalse(self.relay.thread.is_alive());self.assertFalse(self.relay.errors)
    def test_partial_frame_uses_a_finite_deadline(self):
        original=self.relay.read
        self.relay.read=lambda stream,message:original(stream,message,timeout=.05)
        self.request();self.peer.sendall(struct.pack('!I',8)+b'xx')
        self.relay.thread.join(1)
        self.assertFalse(self.relay.thread.is_alive());self.assertEqual(len(self.relay.errors),1)
        self.assertIn('Relay frame deadline expired',str(self.relay.errors.pop()))

if __name__=='__main__':unittest.main(verbosity=2)
