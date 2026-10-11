"""Exercise production BrokerClient framing against an independent protocol peer."""
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
import broker_pb2 as pb
import core_pb2 as core
import grants_pb2 as grant
from harness.wire import frame

PROBE=Path(sys.argv.pop(1))


def receive(stream,kind):
    def exact(n):
        result=b''
        while len(result)<n:
            if not select.select([stream],[],[],3)[0]:raise AssertionError('Protocol peer timeout')
            part=stream.recv(n-len(result))
            if not part:raise EOFError('Protocol peer closed')
            result+=part
        return result
    n=struct.unpack('!I',exact(4))[0]
    if n>1048576:raise AssertionError('Excessive probe output')
    return kind.FromString(exact(n))


class BrokerControlClientTests(unittest.TestCase):
    def setUp(self):
        self.peer,broker=socket.socketpair();self.stream,control=socket.socketpair()
        self.log=tempfile.TemporaryFile();self.sequence=0
        self.proc=subprocess.Popen([str(PROBE),str(broker.fileno()),str(os.getpid())],
            stdin=control,stdout=control,stderr=self.log,pass_fds=(broker.fileno(),))
        broker.close();control.close();self.addCleanup(self.cleanup)
        request=receive(self.peer,pb.Request)
        self.assertTrue(request.management.HasField('runtime_identities'))
        response=pb.Reply(protocol_version=1,request_id=request.request_id,
            management=core.Reply(protocol_version=1,request_id=request.request_id,
                identities=core.RuntimeIdentitiesReply(workspaced_pid=os.getpid())))
        self.peer.sendall(frame(response))
        self.principal=grant.ActorRef(issuer_id=b'i'*32,actor_id=b'a'*32)
        self.app=pb.ApplicationRef(server_id=b's'*32,workspace=b'w',application=b'p')
        self.lease=pb.ApplicationControlLease(id=b'l'*32,principal=self.principal,application=self.app)

    def cleanup(self):
        if self.proc.poll() is None:
            self.stream.shutdown(socket.SHUT_WR)
            self.peer.shutdown(socket.SHUT_WR)
            try:self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3)
        self.stream.close();self.peer.close()
        self.log.seek(0);log=self.log.read().decode(errors='replace');self.log.close()
        for marker in ['AddressSanitizer','LeakSanitizer','runtime error:']:self.assertNotIn(marker,log)

    def send(self,**operation):
        self.sequence+=1
        self.stream.sendall(frame(pb.Request(protocol_version=1,request_id=self.sequence,**operation)))
        return receive(self.peer,pb.Request)

    def respond(self,request,**outcome):
        self.peer.sendall(frame(pb.Reply(protocol_version=1,request_id=request.request_id,**outcome)))

    def result(self,field):
        reply=receive(self.stream,pb.Reply)
        self.assertTrue(reply.HasField(field),reply)
        if field!='control_revoked':self.assertEqual(reply.request_id,self.sequence)
        return getattr(reply,field)

    def acquire(self):
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.respond(request,control_lease=self.lease)
        self.assertEqual(self.result('control_lease'),self.lease)

    def event(self,lease=None,**fields):
        event=pb.Reply(protocol_version=1,control_revoked=lease or self.lease)
        for key,value in fields.items():setattr(event,key,value)
        return frame(event)

    def rejected(self):
        self.assertEqual(self.proc.wait(timeout=3),2)

    def test_acquire_returns_exact_lease(self):self.acquire()

    def test_idle_revocation_is_delivered_without_request(self):
        self.acquire();self.peer.sendall(self.event());self.assertEqual(self.result('control_revoked'),self.lease)

    def test_revocation_before_acquire_reply_stays_revoked(self):
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.peer.sendall(self.event());self.respond(request,control_lease=self.lease)
        self.assertEqual(self.result('control_revoked'),self.lease);self.assertEqual(self.result('control_lease'),self.lease)

    def test_revocation_during_unrelated_request_preserves_reply(self):
        self.acquire();request=self.send(application_access=pb.ApplicationAccessRequest(principal=self.principal,applications=[self.app]))
        self.peer.sendall(self.event());self.respond(request,application_access=pb.ApplicationAccessReply(applications=[pb.ApplicationAccess(application=self.app,observe=True,control=False)]))
        self.result('control_revoked');self.result('application_access')

    def test_partial_event_does_not_block_idle_loop(self):
        self.acquire();event=self.event();self.peer.sendall(event[:2])
        self.assertFalse(select.select([self.stream],[],[],.05)[0])
        self.peer.sendall(event[2:]);self.result('control_revoked')

    def test_partial_event_survives_starting_request(self):
        self.acquire();event=self.event();self.peer.sendall(event[:7])
        request=self.send(control_release=pb.ApplicationControlRelease(id=self.lease.id,principal=self.principal))
        self.peer.sendall(event[7:]);self.respond(request,control_released=pb.ApplicationControlReleased(id=self.lease.id))
        self.result('control_revoked');self.result('control_released')

    def test_duplicate_event_fails_closed(self):
        self.acquire();self.peer.sendall(self.event()+self.event());self.rejected()

    def test_unknown_event_fails_closed(self):self.peer.sendall(self.event());self.rejected()

    def test_wrong_actor_event_fails_closed(self):
        self.acquire();self.lease.principal.actor_id=b'b'*32;self.peer.sendall(self.event());self.rejected()

    def test_wrong_application_event_fails_closed(self):
        self.acquire();self.lease.application.application=b'other';self.peer.sendall(self.event());self.rejected()

    def test_event_requires_zero_request_id(self):
        self.acquire();self.peer.sendall(self.event(request_id=2));self.rejected()

    def test_event_requires_current_protocol(self):
        self.acquire();self.peer.sendall(self.event(protocol_version=2));self.rejected()

    def test_acquire_cannot_substitute_principal(self):
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.lease.principal.actor_id=b'b'*32;self.respond(request,control_lease=self.lease);self.rejected()

    def test_acquire_cannot_substitute_application(self):
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.lease.application.application=b'other';self.respond(request,control_lease=self.lease);self.rejected()

    def test_acquire_requires_full_lease_identity(self):
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.lease.id=b'short';self.respond(request,control_lease=self.lease);self.rejected()

    def test_release_requires_matching_identity(self):
        self.acquire();request=self.send(control_release=pb.ApplicationControlRelease(id=self.lease.id,principal=self.principal))
        self.respond(request,control_released=pb.ApplicationControlReleased(id=b'x'*32));self.rejected()

    def test_released_lease_cannot_revoke_replacement(self):
        self.acquire();request=self.send(control_release=pb.ApplicationControlRelease(id=self.lease.id,principal=self.principal))
        self.respond(request,control_released=pb.ApplicationControlReleased(id=self.lease.id));self.result('control_released')
        self.peer.sendall(self.event());self.rejected()

    def test_reply_requires_matching_request_id(self):
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        request.request_id+=1;self.respond(request,control_lease=self.lease);self.rejected()

    def test_unsolicited_ordinary_reply_fails_closed(self):
        self.peer.sendall(frame(pb.Reply(protocol_version=1,request_id=99,control_lease=self.lease)));self.rejected()

    def test_excessive_idle_frame_fails_closed(self):self.peer.sendall(struct.pack('!I',1048577));self.rejected()

    def test_truncated_idle_frame_fails_closed(self):
        self.peer.sendall(self.event()[:7]);self.peer.shutdown(socket.SHUT_WR);self.rejected()

    def test_broker_denial_keeps_transport_usable(self):
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.respond(request,error=core.Error(code=core.FORBIDDEN,message='denied'));self.result('error');self.acquire()

    def test_early_revocation_cannot_target_different_acquisition(self):
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.peer.sendall(self.event());self.lease.id=b'z'*32;self.respond(request,control_lease=self.lease);self.rejected()

    def test_repeated_acquire_cannot_replace_live_identity(self):
        self.acquire();request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.lease.id=b'z'*32;self.respond(request,control_lease=self.lease);self.rejected()

    def test_repeated_acquire_preserves_identity(self):
        self.acquire();self.acquire()

    def test_revocation_during_repeated_acquire_stays_revoked(self):
        self.acquire();request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.peer.sendall(self.event());self.respond(request,control_lease=self.lease)
        self.result('control_revoked');self.result('control_lease')

    def test_partial_idle_frame_has_a_deadline(self):
        self.peer.sendall(self.event()[:2]);self.assertEqual(self.proc.wait(timeout=13),2)

    def test_empty_principal_cannot_be_confirmed(self):
        self.principal.Clear();self.lease.principal.Clear()
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.respond(request,control_lease=self.lease);self.rejected()

    def test_missing_server_identity_cannot_be_confirmed(self):
        self.app.server_id=b'';self.lease.application.CopyFrom(self.app)
        request=self.send(control_acquire=pb.ApplicationControlRequest(principal=self.principal,application=self.app))
        self.respond(request,control_lease=self.lease);self.rejected()


if __name__=='__main__':unittest.main(verbosity=2)
