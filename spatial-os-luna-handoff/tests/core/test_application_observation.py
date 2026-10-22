"""Authenticated world actors observe real confined C clients through broker grants."""
import json
import os
import shutil
import sqlite3
import socket
import select
import struct
import threading
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
from google.protobuf import text_format
import core_pb2 as core
import grants_pb2 as grant
import broker_pb2 as pb
import world_session_pb2 as session
import security_pb2 as security
from harness.oracles import pose
from harness.wire import frame
import test_application_access as access
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from observation_resource_fixture import fund_actor_application_reservations
from test_world_identity import WorldIdentityFixture, WorldProcess
from test_world_authority import WorldAuthorityTests
from test_workspace_authority import WorkspaceAuthorityTests as Workspace


class BrokerRelay:
    """Relay real broker traffic and inject one response-boundary fault."""
    def __init__(self, broker):
        self.broker=broker; self.proc=broker.proc
        self.stream,self.server=socket.socketpair();self.hook=None;self.calls=0;self.errors=[]
        self._barrier_lock=threading.Lock();self._hold_control_acquire=False
        self.control_acquire_reply_seen=threading.Event();self.control_acquire_entered=threading.Event()
        self.control_acquire_release=threading.Event();self._control_acquire_record=None
        self._closed=threading.Event();self._closing=False
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def read(self, stream, message, timeout=10.0):
        deadline=time.monotonic()+timeout
        def exact(n):
            result=b''
            while len(result)<n:
                if self._closed.is_set():raise EOFError()
                remaining=deadline-time.monotonic()
                if remaining<=0:raise AssertionError('Relay frame deadline expired')
                if not select.select([stream],[],[],min(.05,remaining))[0]:continue
                part=stream.recv(n-len(result))
                if not part: raise EOFError()
                result+=part
            return result
        size=struct.unpack('!I',exact(4))[0]
        if size>1048576:raise AssertionError('Invalid relay frame')
        return message.FromString(exact(size))

    def hold_next_control_acquire(self):
        with self._barrier_lock:
            self.control_acquire_reply_seen.clear();self.control_acquire_entered.clear()
            self.control_acquire_release.clear();self._control_acquire_record=None
            self._hold_control_acquire=True

    def control_acquire_record(self):
        with self._barrier_lock:
            return dict(self._control_acquire_record or {})

    def release_control_acquire(self):
        self.control_acquire_release.set()

    def run(self):
        try:
            pending=None
            while not self._closed.is_set():
                ready,_,_=select.select([self.server,self.broker.stream],[],[],.05)
                if self.server in ready:
                    request=self.read(self.server,pb.Request) # EOF also retires during an outstanding request.
                    if pending is not None:raise AssertionError('Concurrent relay request')
                    pending=request
                    self.broker.stream.sendall(frame(pending))
                if self.broker.stream not in ready:continue
                reply=self.read(self.broker.stream,pb.Reply)
                if reply.HasField('control_revoked'):
                    if reply.protocol_version!=1 or reply.request_id!=0:
                        raise AssertionError('Invalid relay revocation identity')
                    self.server.sendall(frame(reply))
                    continue
                if pending is None or reply.protocol_version!=1 or reply.request_id!=pending.request_id:
                    raise AssertionError('Mismatched relay response identity')
                request=pending;pending=None
                if request.HasField('control_acquire'):
                    lease=reply.control_lease if reply.HasField('control_lease') else None
                    correlated=bool(lease and reply.protocol_version==request.protocol_version==1 and
                        reply.request_id==request.request_id and len(lease.id)==32 and
                        request.control_acquire.HasField('principal') and request.control_acquire.HasField('application') and
                        lease.HasField('principal') and lease.HasField('application') and
                        lease.principal.SerializeToString()==request.control_acquire.principal.SerializeToString() and
                        lease.application.SerializeToString()==request.control_acquire.application.SerializeToString())
                    record={'request_protocol':request.protocol_version,'reply_protocol':reply.protocol_version,
                        'request_id':request.request_id,'reply_id':reply.request_id,
                        'principal':request.control_acquire.principal.SerializeToString(),
                        'application':request.control_acquire.application.SerializeToString(),
                        'lease_id':lease.id if lease else b'',
                        'outcome':reply.WhichOneof('outcome'),'correlated_success':correlated}
                    with self._barrier_lock:
                        hold=self._hold_control_acquire
                        if hold:
                            self._control_acquire_record=record
                            self._hold_control_acquire=False
                    if hold:
                        if correlated:
                            self.control_acquire_entered.set()
                            self.control_acquire_reply_seen.set()
                            if not self.control_acquire_release.wait(8):
                                raise AssertionError('Control-acquire reply barrier was not released')
                        else:
                            self.control_acquire_reply_seen.set()
                if request.HasField('application_access'):
                    self.calls+=1
                    if self.hook:self.hook(self.calls,reply)
                self.server.sendall(frame(reply))
        except (EOFError,OSError):pass
        except BaseException as error:self.errors.append(error)

    def close(self):
        if self._closing:return
        self._closing=True;self._closed.set();self.release_control_acquire()
        try:self.server.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        self.thread.join(5)
        self.stream.close();self.server.close()
        if self.thread.is_alive():raise AssertionError('Broker relay did not stop')
        if self.errors:raise self.errors[0]


class ApplicationObservationTests(WorldIdentityFixture):
    close_brokers = access.ApplicationAccessTests.close_brokers
    setup_application = access.ApplicationAccessTests.setup_application
    launch_application = access.ApplicationAccessTests.launch_application
    data = access.ApplicationAccessTests.data
    principal = Workspace.principal
    workspace_terms = Workspace.workspace_terms
    enroll_second = Workspace.enroll_second
    authenticate_second = Workspace.authenticate_second
    terms = WorldAuthorityTests.terms
    scoped_world = WorldAuthorityTests.scoped_world
    own = Workspace.own
    delegate = Workspace.delegate
    revoke_grant = Workspace.revoke_grant

    def setUp(self):
        super().setUp(); self.brokers=[]; self.sequence=0
        self.expected_world_exit=0
        self.addCleanup(self.close_brokers)

    def setup_world(self, rights=access.ALL, legacy=False, include_third_actor=False):
        if include_third_actor and legacy:
            raise ValueError('A third actor requires the scoped issuer authority fixture')
        configuration=os.environ.get('SPATIAL_TEST_BROKER_CONFIG')
        if configuration:
            resources=text_format.Parse(access.Path(configuration).read_text(),pb.BrokerProcessConfig())
            # This fixture retains its bootstrap application beside two independent native witnesses.
            # Fund the policy selected for workspace one; preserve each application profile limit.
            fund_actor_application_reservations(resources.resources,'one',applications=3)
            path=self.root/'observation-resources.textproto'
            path.write_text(text_format.MessageToString(resources));path.chmod(0o600)
            self.enterContext(patch.dict(os.environ,SPATIAL_TEST_BROKER_CONFIG=str(path)))
        self.enroll_second()
        if include_third_actor:
            self.third_key, self.third_public = self.key('third-independent-controller')
            self.third_record = self.decide(self.owner_peer, self.enroll(self.owner_peer,
                self.intent(public=self.third_public, label='Third independent controller'),
                self.third_key, self.third_public)).enrollment
            self.third = self.claims(self.third_record, self.info)
        self.setup_application(rights)
        with sqlite3.connect(self.execution/'execution.db') as db:
            self.project = access.Path(db.execute('SELECT path FROM worktrees WHERE id=?', ('tree-one',)).fetchone()[0])
        shutil.copy2(access.workspace.WORLD.parent/'spatial-confined-xprobe', self.project/'probe')
        cookie = self.native.supervisor.issue(security.COMPOSITOR, b'', b'').cookie
        data = b'\xff\xff'
        for part in (b'', str(self.native.display).encode(), b'MIT-MAGIC-COOKIE-1', cookie):
            data += struct.pack('!H', len(part))+part
        auth = self.root/'compositor.auth'; auth.write_bytes(data); auth.chmod(0o600)
        self.relay=BrokerRelay(self.broker);self.addCleanup(self.relay.close)
        with patch.dict(os.environ, SPATIAL_TEST_DISPLAY=':'+str(self.native.display), XAUTHORITY=str(auth), LIBGL_ALWAYS_SOFTWARE='1'):
            roots = lambda:[self.terms(), self.terms(self.second, rights=[grant.WORLD_OBSERVE, grant.WORLD_NAVIGATE], depth=0)] + (
                [self.terms(self.third, rights=[grant.WORLD_OBSERVE, grant.WORLD_NAVIGATE], depth=0)] if include_third_actor else [])
            self.world_process = self.scoped_world(session.REGISTERED_ACTORS, native=True,
                broker=self.relay, roots=roots, peer_count=3 if include_third_actor else 2)
            if legacy:
                old=self.world_process;old.close()
                config=session.WorldIdentityConfig();text_format.Parse(old.config_path.read_text(),config)
                config.ClearField('authority_directory')
                for identity in (self.identity,self.second):
                    config.actor_grants.add(actor_id=identity.actor_id,grants=['observe','navigate','interact'])
                old.config_path.write_text(text_format.MessageToString(config))
                self.world_process=WorldProcess(self,old.config_path,old.state_path,native=True,broker=self.relay)
                self.worlds.append(self.world_process)
                self.world_process.management('system.hello')
                self.assertTrue(self.authenticate(self.world_process.peers[0]).HasField('opened'))
        self.addCleanup(self.close_world)
        self.peer = self.world_process.peers[0]
        self.viewer = self.world_process.peers[1]
        self.assertTrue(self.authenticate_second(self.viewer).HasField('opened'))
        if include_third_actor:
            self.third_peer = self.world_process.peers[2]
            self.assertTrue(self.authenticate(self.third_peer, self.third_record, self.third_key, self.third_public).HasField('opened'))
        self.world_process.management('workspace.available', id='one', available=True)
        self.assertTrue(self.peer.act(workspace_select=core.WorkspaceIdRequest(id='one')).HasField('action'))
        self.probe_number = 0
        return self.world_process

    def close_world(self):
        self.world_process.close()
        self.assertEqual(self.world_process.proc.returncode,self.expected_world_exit)
        for path in self.root.glob('world-*.log'):
            text=path.read_text(errors='replace')
            for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,text)
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=access.Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-'+path.name)).write_text(text)

    def probe(self, position=(0, 0, -2)):
        self.probe_number += 1; name = 'observed-'+str(self.probe_number)
        code = ("import subprocess,time\nfrom pathlib import Path\n"
                "out=open('/state/data/probe.jsonl','w')\n"
                f"p=subprocess.Popen(['/project/probe','--id',{name!r}],stdin=subprocess.PIPE,stdout=out,stderr=out)\n"
                "while not Path('/state/data/release').exists():\n"
                " if p.poll() is not None: raise SystemExit(p.returncode or 1)\n"
                " time.sleep(.01)\n"
                "p.terminate();p.wait();out.close()\n")
        result = self.peer.act(process_launch=core.ProcessLaunchRequest(operation_id=name,
            profile='development', argv=['/usr/bin/python3','-c',code]))
        self.assertTrue(result.HasField('action'), result)
        app = pb.ApplicationRef(workspace=b'one', application=result.action.process.process_id.encode(), server_id=self.server_id)
        log = self.data(app)/'probe.jsonl'
        def preserve_probe():
            if log.exists() and os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target=access.Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-'+name+'.log')).write_bytes(log.read_bytes())
        self.addCleanup(preserve_probe)
        try:
            value = self.until(lambda:self.world_process.management('surface.find',probe_id=name),lambda r:r['surface'] is not None)['surface']
        except AssertionError as error:
            self.fail(str(error)+'\n'+(log.read_text() if log.exists() else 'No native probe output'))
        self.world_process.management('surface.place',id=value['id'],room=self.world_info['entry_room'],pose=pose(position),size=[1,1])
        self.assertTrue(any(json.loads(line).get('event')=='ready' for line in log.read_text().splitlines()))
        return app,value

    def grant_viewer(self, rights, app=None, components=None, claims=None):
        scope = components if components is not None else ([] if app is None else [b'applications',app.application])
        reply = self.delegate(self.peer,self.workspace_terms(claims or self.second,rights=rights,depth=0,components=scope))
        self.assertTrue(reply.HasField('grant'),reply);return reply.grant

    def visible(self, peer=None):
        reply = (peer or self.viewer).act(observe=core.ActorObserveRequest(width=128,height=128))
        self.assertTrue(reply.HasField('action'),reply)
        return {item.id for item in reply.action.observation.visible if item.HasField('pixels')}

    def test_explicit_observation_shows_real_application_without_selection(self):
        self.setup_world(); app,surface=self.probe()
        self.grant_viewer([grant.WORKSPACE_ACCESS]);self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        self.assertIn(surface['id'],self.visible())
        state=self.viewer.call(inspect=session.WorldSessionInspect()).state
        self.assertFalse(state.workspace)

    def test_world_admission_and_placement_do_not_grant_application_observation(self):
        self.setup_world();app,surface=self.probe()
        self.assertIn(surface['id'],self.visible(self.peer))
        self.assertNotIn(surface['id'],self.visible())

    def test_workspace_access_alone_does_not_reveal_application(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.WORKSPACE_ACCESS])
        self.assertIn(surface['id'],self.visible(self.peer));self.assertNotIn(surface['id'],self.visible())

    def test_observe_grant_without_workspace_access_does_not_reveal_application(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        self.assertIn(surface['id'],self.visible(self.peer));self.assertNotIn(surface['id'],self.visible())

    def test_application_scope_filters_other_live_application(self):
        self.setup_world();app,one=self.probe((-.6,0,-2));other,two=self.probe((.6,0,-2))
        self.grant_viewer([grant.WORKSPACE_ACCESS]);self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        self.assertEqual(self.visible(),{one['id']})
        self.assertEqual(self.visible(self.peer),{one['id'],two['id']})

    def test_observe_revocation_removes_metadata_but_preserves_process(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.WORKSPACE_ACCESS])
        record=self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        self.assertIn(surface['id'],self.visible());self.revoke_grant(self.peer,record)
        self.assertNotIn(surface['id'],self.visible());self.assertIn(surface['id'],self.visible(self.peer))
        self.assertTrue(self.world_process.management('surface.get',id=surface['id'])['mapped'])

    def test_authorized_aim_uses_same_filtered_scene(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.WORKSPACE_ACCESS])
        self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        reply=self.viewer.act(aim=core.ActorAimRequest(direction=core.Vec3(z=-1)))
        self.assertTrue(reply.HasField('action'),reply);self.assertEqual(reply.action.aim.hit.id,surface['id'])

    def test_observe_does_not_enable_input_control(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.WORKSPACE_ACCESS])
        self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        self.assertIn(surface['id'],self.visible())
        self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.FORBIDDEN)

    def test_post_render_revocation_returns_fenced_denial_without_observation(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.WORKSPACE_ACCESS])
        record=self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        receipts=[]
        def revoke_after_post_render_decision(count,reply):
            if count==2:
                self.assertTrue(reply.application_access.applications[0].observe)
                receipts.append(access.ApplicationAccessTests.revoke_record(self,record))
        self.relay.calls=0;self.relay.hook=revoke_after_post_render_decision
        reply=self.viewer.act(observe=core.ActorObserveRequest(width=128,height=128))
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(reply.error.code,core.FORBIDDEN)
        self.assertEqual(reply.error.message,'Workspace authority changed during operation')
        self.assertFalse(reply.HasField('action'))
        self.assertEqual(len(receipts),1)
        self.assertEqual(receipts[0].id,record.id)
        self.assertEqual(access.ApplicationAccessTests.access(self,app,claims=self.second),[(False,False)])
        self.assertNotIn(surface['id'],self.visible())
        self.assertIn(surface['id'],self.visible(self.peer))

    def test_revocation_while_observation_reply_is_queued_closes_channel(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.WORKSPACE_ACCESS])
        record=self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        receipts=[]
        def revoke_during_queued_reply_check(count,reply):
            # Calls 1 and 2 belong to scene filtering and the post-render fence.
            # Call 3 occurs when the world pump validates the queued response.
            if count==3:
                self.assertTrue(reply.application_access.applications[0].observe)
                receipts.append(access.ApplicationAccessTests.revoke_record(self,record))
        self.relay.calls=0;self.relay.hook=revoke_during_queued_reply_check
        with self.assertRaises(EOFError):
            self.viewer.act(observe=core.ActorObserveRequest(width=128,height=128))
        self.assertGreaterEqual(self.relay.calls,3)
        self.assertEqual(len(receipts),1)
        self.assertEqual(receipts[0].id,record.id)
        self.assertIn(surface['id'],self.visible(self.peer))

    def test_revocation_before_post_render_check_returns_no_metadata(self):
        self.setup_world();app,surface=self.probe();self.grant_viewer([grant.WORKSPACE_ACCESS])
        record=self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        def revoke_after_first_decision(count,reply):
            if count==1:access.ApplicationAccessTests.revoke_record(self,record)
        self.relay.calls=0;self.relay.hook=revoke_after_first_decision
        self.denied(self.viewer.act(observe=core.ActorObserveRequest(width=128,height=128)),core.FORBIDDEN)
        self.assertNotIn(surface['id'],self.visible())

    def invalid_broker_reply(self, fault):
        self.setup_world();app,surface=self.probe()
        self.grant_viewer([grant.WORKSPACE_ACCESS]);self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        def corrupt(count,reply):
            if count==1:fault(reply.application_access)
        self.relay.calls=0;self.relay.hook=corrupt;self.expected_world_exit=2
        try:
            reply=self.viewer.act(observe=core.ActorObserveRequest(width=128,height=128))
            self.denied(reply,core.INTERNAL_ERROR)
        except (EOFError,ConnectionResetError):pass
        self.assertEqual(self.world_process.proc.wait(timeout=5),2)

    def test_broker_reply_cannot_substitute_application_identity(self):
        self.invalid_broker_reply(lambda reply:setattr(reply.applications[0].application,'application',b'other'))

    def test_broker_reply_requires_explicit_observation_decision(self):
        self.invalid_broker_reply(lambda reply:reply.applications[0].ClearField('observe'))

    def test_broker_reply_requires_matching_group_count(self):
        self.invalid_broker_reply(lambda reply:reply.ClearField('applications'))

    def test_broker_reply_cannot_grant_control_without_observation(self):
        def fault(reply):reply.applications[0].observe=False;reply.applications[0].control=True
        self.invalid_broker_reply(fault)

    def test_legacy_world_grants_do_not_reveal_labeled_application(self):
        self.setup_world(legacy=True);app,surface=self.probe()
        self.assertIn(surface['id'],self.visible(self.peer));self.assertNotIn(surface['id'],self.visible())

    def test_legacy_interaction_grant_cannot_control_labeled_application(self):
        self.setup_world(legacy=True);app,surface=self.probe()
        self.grant_viewer([grant.WORKSPACE_ACCESS]);self.grant_viewer([grant.APPLICATION_OBSERVE],app)
        self.assertIn(surface['id'],self.visible())
        reply=self.viewer.act(aim=core.ActorAimRequest(direction=core.Vec3(z=-1)))
        self.assertEqual(reply.action.aim.hit.id,surface['id'])
        self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)),core.FORBIDDEN)


if __name__ == '__main__': unittest.main(defaultTest='ApplicationObservationTests', verbosity=2)
