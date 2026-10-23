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

# Compile the actual transport methods without launching native fixture setup.
TRANSPORT=SOURCE.with_name('test_workspace_authority.py')
transport_node=next(n for n in ast.parse(TRANSPORT.read_text()).body
                    if isinstance(n,ast.ClassDef) and n.name=='BrokerProcess')
transport_methods={'initialize_transport','dispatch_control','exchange','request'}
transport_node.body=[n for n in transport_node.body
                     if isinstance(n,ast.FunctionDef) and n.name in transport_methods]
assert {n.name for n in transport_node.body}==transport_methods
WIRE=SOURCE.with_name('test_world_identity.py')
receive_node=next(n for n in ast.parse(WIRE.read_text()).body
                 if isinstance(n,ast.FunctionDef) and n.name=='receive')
transport_namespace=dict(threading=threading,broker=pb,frame=frame,select=select,struct=struct,time=time)
exec(compile(ast.Module(body=[receive_node,transport_node],type_ignores=[]),str(TRANSPORT),'exec'),transport_namespace)
BrokerProcess=transport_namespace['BrokerProcess']

class ObservedLock:
    """Expose contention while preserving the actual reentrant mutex semantics."""
    def __init__(self, lock):self.lock=lock;self.contended=threading.Event()
    def acquire(self, blocking=True, timeout=-1):
        if self.lock.acquire(False):return True
        self.contended.set()
        if not blocking:return False
        return self.lock.acquire(timeout=timeout)
    def release(self):self.lock.release()
    def __enter__(self):self.acquire();return self
    def __exit__(self,*args):self.release()

class BrokerRelayWireTests(unittest.TestCase):
    def setUp(self):
        broker, self.peer=socket.socketpair();self.peer.settimeout(2)
        self.addCleanup(self.peer.close);self.addCleanup(broker.close)
        self.broker=BrokerProcess();self.broker.stream=broker;self.broker.proc=None;self.broker.sequence=0
        self.broker.initialize_transport(world_channel_opt_in=False)
        self.broker.io_lock=ObservedLock(self.broker.io_lock)
        self.relay=BrokerRelay(self.broker)
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
    def start_call(self, call):
        result=SimpleNamespace(done=threading.Event(),values=[],errors=[])
        def run():
            try:result.values.append(call())
            except BaseException as error:result.errors.append(error)
            finally:result.done.set()
        result.thread=threading.Thread(target=run,daemon=True);result.thread.start()
        self.addCleanup(result.thread.join,2)
        return result
    def finish_call(self, result):
        self.assertTrue(result.done.wait(2),'Direct broker caller did not finish')
        result.thread.join(2);self.assertFalse(result.thread.is_alive())
        self.assertFalse(result.errors,result.errors)
        self.assertEqual(len(result.values),1);return result.values[0]
    def direct(self):
        operation=pb.Request().application_access
        operation.principal.CopyFrom(self.lease().principal)
        return self.broker.request(application_access=operation)
    def answer(self, request_id, label):
        result=pb.Reply(protocol_version=1,request_id=request_id);result.error.message=label
        return result
    def pause_direct_read(self):
        entered=threading.Event();release=threading.Event();original=transport_namespace['receive']
        def receive(read,ready,message):
            if not entered.is_set():
                entered.set()
                if not release.wait(2):raise AssertionError('Direct-read barrier expired')
            return original(read,ready,message)
        transport_namespace['receive']=receive
        self.addCleanup(transport_namespace.__setitem__,'receive',original)
        self.addCleanup(release.set)
        return entered,release
    def assert_upstream_released(self):
        self.assertTrue(self.broker.io_lock.acquire(False),'Relay retained the broker lock')
        self.broker.io_lock.release()
        self.assertGreaterEqual(self.broker.stream.fileno(),0)

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
        self.assert_upstream_released()
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
        self.assert_upstream_released()
    def test_partial_frame_uses_a_finite_deadline(self):
        original=self.relay.read
        self.relay.read=lambda stream,message:original(stream,message,timeout=.05)
        self.request();self.peer.sendall(struct.pack('!I',8)+b'xx')
        self.relay.thread.join(1)
        self.assertFalse(self.relay.thread.is_alive());self.assertEqual(len(self.relay.errors),1)
        self.assertIn('Relay frame deadline expired',str(self.relay.errors.pop()))
        self.assert_upstream_released()

    def test_idle_relay_cannot_steal_a_direct_reply_or_reuse_stale_readiness(self):
        entered,release=self.pause_direct_read();direct=self.start_call(self.direct)
        request=self.relay.read(self.peer,pb.Request);self.assertTrue(entered.wait(2))
        reply=self.answer(request.request_id,'direct')
        self.peer.sendall(frame(reply))
        self.assertTrue(self.broker.io_lock.contended.wait(2))
        self.assertFalse(direct.done.is_set());self.assertFalse(self.relay.errors)
        release.set();self.assertEqual(self.finish_call(direct),reply)
        # This must still progress after the direct caller consumed idle readiness.
        self.request();world=self.answer(41,'world');self.peer.sendall(frame(world))
        self.assertEqual(self.receive(),world);self.assertTrue(self.relay.thread.is_alive())

    def test_world_request_waits_for_direct_exchange_even_with_equal_request_ids(self):
        entered,release=self.pause_direct_read();direct=self.start_call(self.direct)
        request=self.relay.read(self.peer,pb.Request);self.assertTrue(entered.wait(2))
        world=pb.Request(protocol_version=1,request_id=request.request_id)
        world.application_access.principal.CopyFrom(self.lease().principal)
        self.relay.stream.sendall(frame(world))
        self.assertTrue(self.broker.io_lock.contended.wait(2))
        self.assertFalse(select.select([self.peer],[],[],.05)[0])
        direct_reply=self.answer(request.request_id,'direct');self.peer.sendall(frame(direct_reply))
        release.set();self.assertEqual(self.finish_call(direct),direct_reply)
        self.assertEqual(self.relay.read(self.peer,pb.Request),world)
        world_reply=self.answer(world.request_id,'world');self.peer.sendall(frame(world_reply))
        self.assertEqual(self.receive(),world_reply)

    def test_hook_direct_exchange_forwards_revocation_exactly_once(self):
        rows=[]
        self.relay.hook=lambda count,reply:rows.append((count,reply,self.direct()))
        self.request();world=self.answer(41,'world');self.peer.sendall(frame(world))
        direct_request=self.relay.read(self.peer,pb.Request)
        direct_reply=self.answer(direct_request.request_id,'hook');notice=self.notice()
        self.peer.sendall(frame(notice)+frame(direct_reply))
        self.assertEqual(self.receive(),notice);self.assertEqual(self.receive(),world)
        self.assertEqual(rows,[(1,world,direct_reply)])
        self.assertFalse(select.select([self.relay.stream],[],[],.05)[0])

    def test_control_acquire_barrier_releases_upstream_for_direct_exchange_and_notice(self):
        self.relay.hold_next_control_acquire();self.request(acquire=True)
        world=pb.Reply(protocol_version=1,request_id=41);world.control_lease.CopyFrom(self.lease())
        self.peer.sendall(frame(world));self.assertTrue(self.relay.control_acquire_entered.wait(2))
        direct=self.start_call(self.direct);request=self.relay.read(self.peer,pb.Request)
        reply=self.answer(request.request_id,'during-barrier');notice=self.notice()
        self.peer.sendall(frame(notice)+frame(reply))
        self.assertEqual(self.finish_call(direct),reply);self.assertEqual(self.receive(),notice)
        self.assertFalse(self.relay.control_acquire_release.is_set())
        self.assertFalse(select.select([self.relay.stream],[],[],.05)[0])
        self.relay.release_control_acquire();self.assertEqual(self.receive(),world)

    def test_close_cancels_upstream_lock_wait_and_deregisters_only_its_sink(self):
        with self.broker.io_lock:
            request=pb.Request(protocol_version=1,request_id=41);request.application_access.SetInParent()
            self.relay.stream.sendall(frame(request))
            self.assertTrue(self.broker.io_lock.contended.wait(2))
            sentinel=object();self.broker.channels.append(sentinel)
            started=time.monotonic();self.relay.close()
            self.assertLess(time.monotonic()-started,1)
            self.assertFalse(select.select([self.peer],[],[],0)[0])
            self.assertEqual(self.broker.channels,[sentinel]);self.assert_upstream_released()

    def test_pending_world_eof_poison_rejects_direct_request_before_send(self):
        request=self.request();self.relay.stream.shutdown(socket.SHUT_WR)
        self.relay.thread.join(2);self.assertFalse(self.relay.thread.is_alive())
        self.assert_upstream_released()
        # Even equal IDs must never make an abandoned reply look like direct success.
        self.broker.sequence=request.request_id-1
        self.peer.sendall(frame(self.answer(request.request_id,'abandoned')))
        with self.assertRaisesRegex(AssertionError,'Broker transport is unusable'):
            self.direct()
        self.assertFalse(select.select([self.peer],[],[],.05)[0])
        self.assertFalse(self.relay.errors)

    def test_clean_idle_close_preserves_direct_transport_reuse(self):
        self.relay.close();self.assertIsNone(self.broker.transport_error)
        self.assertEqual(self.broker.channels,[]);self.assert_upstream_released()
        direct=self.start_call(self.direct)
        # The relay's cancellable reader deliberately refuses reads after close.
        request=transport_namespace['receive'](self.peer.recv,self.peer,pb.Request)
        reply=self.answer(request.request_id,'after-clean-close');self.peer.sendall(frame(reply))
        self.assertEqual(self.finish_call(direct),reply)

    def test_replacement_relay_refuses_transport_after_abandoned_exchange(self):
        self.request();self.relay.stream.shutdown(socket.SHUT_WR)
        self.relay.thread.join(2);self.assertFalse(self.relay.thread.is_alive())
        self.relay.close();self.assertEqual(self.broker.channels,[])
        with self.assertRaisesRegex(AssertionError,'Broker transport is unusable'):
            BrokerRelay(self.broker)
        self.assert_upstream_released()
        self.assertEqual(self.broker.channels,[])
        self.assertFalse(select.select([self.peer],[],[],.05)[0])

if __name__=='__main__':unittest.main(verbosity=2)
