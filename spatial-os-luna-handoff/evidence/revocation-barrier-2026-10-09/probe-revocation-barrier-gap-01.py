"""Diagnostic only: a committed issuer revoke is not a world cancellation barrier."""
import json,os,pathlib,socket,struct,threading,time,unittest
import core_pb2 as core
import issuer_pb2 as issuer
from harness.wire import frame
from test_world_remote import RemoteWorldTests,RemoteWorldProcess
from test_world_authority import RelayingIssuer

class HeldRevocationRelay(RelayingIssuer):
    def __init__(self,fixture):
        self.held=threading.Event();self.release=threading.Event()
        super().__init__(fixture,lambda:None)
    def relay(self,raw):
        upstream=None;stream=raw
        try:
            raw.settimeout(2);stream=self.context.wrap_socket(raw,server_side=True);self.streams.append(stream)
            upstream=self.fixture.connect()
            def exact(size):
                data=b''
                while len(data)<size:
                    part=stream.recv(size-len(data))
                    if not part:raise EOFError()
                    data+=part
                return data
            while not self.stop.is_set():
                size=struct.unpack('!I',exact(4))[0]
                if size>1048576:raise AssertionError('Excessive relay frame')
                request=issuer.IssuerRequest.FromString(exact(size));sequence=request.request_id
                reply=upstream.request(request);reply.request_id=sequence
                if reply.HasField('revocations') and reply.revocations.events:
                    self.held.set()
                    deadline=time.monotonic()+.5
                    while not self.release.wait(.005):
                        if self.stop.is_set() or time.monotonic()>deadline:break
                stream.sendall(frame(reply))
                if reply.HasField('revocations') and reply.revocations.events:self.delivered.set()
        except (OSError,EOFError):pass
        except BaseException as error:self.errors.append(error)
        finally:
            stream.close()
            if upstream:upstream.close()
    def close(self):
        self.release.set();super().close()

class GapProbe(RemoteWorldTests):
    def test_commit_reply_precedes_remote_actor_cancellation(self):
        relay=HeldRevocationRelay(self)
        local=self.scoped_world(issuer_port=relay.port,authenticate=False);local.close()
        world=RemoteWorldProcess(self,local);self.worlds.append(world);world.management('system.hello')
        peer=world.connect(self);opened=self.authenticate(peer)
        self.assertTrue(opened.HasField('opened'),opened);actor=opened.opened.actor_id
        self.assertTrue(self.navigation(peer).HasField('action'))
        self.revoke()
        committed=time.monotonic()
        self.assertTrue(relay.held.wait(.3),'Expected a genuine committed issuer event at the relay')
        self.assertFalse(relay.delivered.is_set())
        # Management is an independent witness. Do not issue another protected actor action.
        start=world.management('world.get',id=actor)['pose']['p'][0]
        time.sleep(.10)
        finish=world.management('world.get',id=actor)['pose']['p'][0]
        self.assertFalse(relay.delivered.is_set(),'Diagnostic delay expired before the observation')
        self.assertGreater(finish-start,.02,'Did not reproduce continued motion after issuer commit reply')
        relay.release.set();self.assertTrue(relay.delivered.wait(.3))
        time.sleep(.08)
        stopped=world.management('world.get',id=actor)['pose']['p'][0]
        time.sleep(.08)
        still=world.management('world.get',id=actor)['pose']['p'][0]
        self.assertAlmostEqual(stopped,still,places=8,msg='Motion did not stop after revocation delivery')
        record={'status':'gap_reproduced','meaning':'issuer commit reply alone is not cancellation completion',
            'remote_actor_tls':'TLSv1.3','issuer_revision':1,'motion_after_commit_reply':finish-start,
            'position_after_delivery':[stopped,still],'seconds_since_commit_reply':time.monotonic()-committed,
            'relay_scope':'holds one real event reply for at most500ms; does not forge authority',
            'M02_complete':False}
        pathlib.Path(os.environ['SPATIAL_BARRIER_GAP_REPORT']).write_text(json.dumps(record,indent=2)+'\n')
        print(json.dumps(record),flush=True)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([GapProbe('test_commit_reply_precedes_remote_actor_cancellation')]))
    raise SystemExit(not result.wasSuccessful())
