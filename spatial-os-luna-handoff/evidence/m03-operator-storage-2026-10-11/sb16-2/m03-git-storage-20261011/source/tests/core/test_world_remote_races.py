"""Remote actor cancellation during genuine delayed issuer replies."""
import socket
import struct
import time
import unittest
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.wire import frame
from test_world_remote import RemoteWorldProcess,RemoteWorldTests
from test_world_bootstrap_races import BootstrapRelay,WorldBootstrapRaceTests


class DisconnectRelay(BootstrapRelay):
    def relay(self,raw):
        upstream=None;stream=raw
        try:
            raw.settimeout(2);stream=self.context.wrap_socket(raw,server_side=True);self.streams.append(stream)
            upstream=self.fixture.connect()
            def exact(n):
                result=b''
                while len(result)<n:
                    part=stream.recv(n-len(result))
                    if not part:raise EOFError()
                    result+=part
                return result
            while not self.stop.is_set():
                size=struct.unpack('!I',exact(4))[0]
                if size>1048576:raise AssertionError('Excessive relay frame')
                request=issuer.IssuerRequest.FromString(exact(size));sequence=request.request_id
                reply=upstream.request(request);reply.request_id=sequence
                if request.HasField(self.operation) and self.armed.is_set():
                    self.armed.clear();self.revoke();self.remaining=0;self.delivered.set();time.sleep(.15)
                stream.sendall(frame(reply))
        except (OSError,EOFError):pass
        except BaseException as error:self.errors.append(error)
        finally:
            stream.close()
            if upstream:upstream.close()


class RemoteRaceTests(RemoteWorldTests):
    receipt_count=WorldBootstrapRaceTests.receipt_count

    def prepared(self,relay):
        local=self.scoped_world(issuer_port=relay.port);self.publish();self.binding(local)
        self.select_profile();enrollment=self.enroll_bootstrap();local.close()
        world=RemoteWorldProcess(self,local);world.config_path=local.config_path
        self.worlds.append(world);world.management('system.hello')
        owner=world.connect(self);self.assertTrue(self.authenticate(owner).HasField('opened'))
        child=world.connect(self)
        return world,owner,child,enrollment

    def test_remote_credential_revocation_during_profile_reply_creates_no_grant(self):
        holder={}
        def revoke():
            target=issuer.IdentityReference(kind=issuer.CREDENTIAL_TARGET,id=holder['enrollment'][3].credential_id)
            op=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(target=target,expected_security_revision=0,
                reason='remote delayed profile regression'))
            peer=self.connect();reply=self.execute(peer,self.challenge(peer,op));self.assertTrue(reply.HasField('identity_status'))
        relay=BootstrapRelay(self,revoke,'profile_read')
        world,owner,child,enrollment=self.prepared(relay);holder['enrollment']=enrollment;relay.armed.set()
        result=self.authenticate_bootstrap(child,enrollment)
        self.assertTrue(result.HasField('error'),result);self.assertIn(result.error.code,(core.UNAUTHORIZED,core.UNAVAILABLE))
        self.assertTrue(relay.delivered.is_set());self.assertEqual(relay.remaining,0)
        self.assertEqual(self.receipt_count(world),0)
        self.assertTrue(self.navigation(owner).HasField('action'))

    def test_remote_disconnect_during_profile_reply_creates_no_grant(self):
        holder={}
        def disconnect():holder['child'].stream.shutdown(socket.SHUT_RDWR)
        relay=DisconnectRelay(self,disconnect,'profile_read')
        world,owner,child,enrollment=self.prepared(relay);holder['child']=child;relay.armed.set()
        with self.assertRaises((EOFError,OSError)):self.authenticate_bootstrap(child,enrollment)
        self.assertTrue(relay.delivered.wait(2));self.assertEqual(relay.remaining,0)
        # This genuine owner request completes after the delayed issuer reply is consumed.
        self.assertTrue(owner.call(inspect=session.WorldSessionInspect()).HasField('state'))
        self.assertEqual(self.receipt_count(world),0)
        self.assertTrue(self.navigation(owner).HasField('action'))


if __name__=='__main__':
    tests=unittest.TestSuite(RemoteRaceTests(name) for name in RemoteRaceTests.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(tests).wasSuccessful())
