"""Persistent policy controls the production world actor engine."""
import os
import sqlite3
import socket
import ssl
import struct
import subprocess
import threading
import time
import unittest
from google.protobuf import text_format
import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.oracles import pose
from harness.probe import Probe
from harness.wire import frame
from test_world_identity import WorldIdentityFixture, WorldProcess, WORLD

RIGHTS = (grant.WORLD_ENTER, grant.WORLD_NAVIGATE, grant.WORLD_OBSERVE, grant.WORLD_MANIPULATE, grant.POLICY_ADMINISTER)


class RelayingIssuer:
    """Forward real issuer requests. Delay one status request across real revocation."""
    def __init__(self, fixture, revoke, wait_for_revocation_event=True):
        self.fixture = fixture; self.revoke = revoke; self.wait_for_revocation_event = wait_for_revocation_event
        self.armed = threading.Event(); self.triggered = threading.Event()
        self.delivered = threading.Event(); self.stop = threading.Event(); self.errors = []
        self.status_lock = threading.Lock(); self.status_seen = 0; self.trigger_after_status = 1
        self.streams = []; self.workers = []
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.context.load_cert_chain(str(fixture.server_cert), str(fixture.server_key))
        self.context.load_verify_locations(str(fixture.service_cert)); self.context.verify_mode = ssl.CERT_REQUIRED
        self.listener = socket.socket(); self.listener.bind(('127.0.0.1', 0)); self.listener.listen(4)
        self.listener.settimeout(.1); self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.accept, daemon=True); self.thread.start()
        fixture.addCleanup(self.close)

    def accept(self):
        while not self.stop.is_set():
            try: raw, _ = self.listener.accept()
            except socket.timeout: continue
            except OSError: return
            worker = threading.Thread(target=self.relay, args=(raw,), daemon=True)
            self.workers.append(worker); worker.start()

    def relay(self, raw):
        upstream = None; stream = raw
        try:
            raw.settimeout(2); stream = self.context.wrap_socket(raw, server_side=True); self.streams.append(stream)
            upstream = self.fixture.connect()
            def exact(size):
                data = b''
                while len(data) < size:
                    part = stream.recv(size-len(data))
                    if not part: raise EOFError()
                    data += part
                return data
            while not self.stop.is_set():
                size = struct.unpack('!I', exact(4))[0]
                if size > 1048576: raise AssertionError('Excessive relay frame')
                request = issuer.IssuerRequest.FromString(exact(size)); sequence = request.request_id
                should_revoke = False
                if request.HasField('actor_status') and self.armed.is_set():
                    with self.status_lock:
                        self.status_seen += 1
                        should_revoke = self.status_seen == self.trigger_after_status
                    if should_revoke:
                        self.armed.clear(); self.triggered.set(); self.revoke()
                        if self.wait_for_revocation_event:
                            if not self.delivered.wait(.3): raise AssertionError('Real revocation was not forwarded')
                            # Keep the issuer call pending while worldd consumes the watcher event.
                            time.sleep(.08)
                reply = upstream.request(request); reply.request_id = sequence
                stream.sendall(frame(reply))
                if reply.HasField('revocations') and reply.revocations.events: self.delivered.set()
        except (OSError, EOFError): pass
        except BaseException as error: self.errors.append(error)
        finally:
            stream.close()
            if upstream: upstream.close()

    def close(self):
        self.stop.set(); self.listener.close()
        for stream in self.streams:
            try: stream.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        self.thread.join(3)
        for worker in self.workers: worker.join(3)
        if self.thread.is_alive() or any(worker.is_alive() for worker in self.workers):
            raise AssertionError('Issuer relay did not stop')
        if self.errors: raise self.errors[0]


class WorldAuthorityTests(WorldIdentityFixture):
    def principal(self, claims=None):
        claims = claims or self.identity
        return grant.ActorRef(issuer_id=claims.issuer_id, actor_id=claims.actor_id)

    def terms(self, claims=None, rights=RIGHTS, depth=3, components=(), delegable=None):
        return grant.GrantTerms(subject=self.principal(claims),
            scope=grant.Scope(domain=grant.Domain(kind=grant.WORLD, id=self.world_info['world_id']), components=components),
            rights=rights, delegable_rights=rights if delegable is None and depth else delegable or (), delegation_depth=depth)

    def enroll_second(self):
        self.second_key, self.second_public = self.key('second')
        self.second_record = self.decide(self.owner_peer, self.enroll(self.owner_peer,
            self.intent(public=self.second_public), self.second_key, self.second_public)).enrollment
        self.second = self.claims(self.second_record, self.info)

    def authenticate_second(self, peer):
        return self.authenticate(peer, self.second_record, self.second_key, self.second_public)

    def scoped_world(self, admission=session.EXPLICIT_ACTORS, native=False, roots=None, issuer_port=None, authenticate=True, broker=None):
        number = len(self.worlds); state = self.root/f'scoped-world-{number}'
        initial = self.world(state=state)
        self.world_info = initial.management('world.info'); initial.close()
        directory = self.root/f'authority-{number}'; directory.mkdir(mode=0o700)
        seed = session.WorldAuthoritySeed(protocol_version=1, authority_directory=str(directory),
            roots=roots() if roots else [self.terms()])
        path = self.root/f'seed-{number}.textproto'; path.write_text(text_format.MessageToString(seed)); path.chmod(0o600)
        result = subprocess.run([str(WORLD), '--state-dir', str(state), '--init-authority', str(path)], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = session.WorldIdentityConfig(protocol_version=1, issuer_client_config=str(self.config_file(endpoint_port=issuer_port)),
            admission=admission, authority_directory=str(directory))
        self.configure_world_participant(config,state)
        config_path = self.root/f'scoped-{number}.textproto'; config_path.write_text(text_format.MessageToString(config)); config_path.chmod(0o600)
        runtime = core.RuntimeConfig(); text_format.Parse((WORLD.parent/'runtime.textproto').read_text(), runtime)
        runtime.input_timeout = 10
        runtime_path = self.root/f'runtime-{number}.textproto'; runtime_path.write_text(text_format.MessageToString(runtime))
        world = WorldProcess(self, config_path, state, native=native, runtime_config=runtime_path, broker=broker)
        self.worlds.append(world); world.management('system.hello')
        world.state_path = state; world.config_path = config_path; world.runtime_path = runtime_path
        if authenticate: self.assertTrue(self.authenticate(world.peers[0]).HasField('opened'))
        return world

    def own(self, peer, **options):
        reply = peer.call(grants=session.WorldGrantList(**options)); self.assertTrue(reply.HasField('grants'), reply)
        return reply.grants

    def root_grant(self, peer):
        return next(record for record in self.own(peer).grants if not record.parent_id)

    def delegate(self, peer, parent, terms, revision=None):
        revision = self.own(peer).revision if revision is None else revision
        peer.action_sequence += 1
        return peer.call(delegate=session.WorldGrantDelegate(seq=peer.action_sequence, parent_id=parent.id,
            terms=terms, expected_revision=revision))

    def revoke_grant(self, peer, record):
        revision = self.own(peer).revision; peer.action_sequence += 1
        return peer.call(revoke=session.WorldGrantRevoke(seq=peer.action_sequence, id=record.id, expected_revision=revision))

    def navigation(self, peer):
        return peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0]))

    def object(self, world, identity='object-one'):
        world.management('world.create', entity={'id':identity, 'kind':'object', 'room':self.world_info['entry_room'], 'pose':pose((0,0,-2))})
        return identity

    def grant_to_second(self, world, rights, components=()):
        first = world.peers[0]
        result = self.delegate(first, self.root_grant(first), self.terms(self.second, rights=rights, depth=0, components=components))
        self.assertTrue(result.HasField('grant'), result)
        return result.grant

    def test_explicit_seed_and_real_actor_navigation(self):
        world = self.scoped_world(); peer = world.peers[0]
        root = self.root_grant(peer); self.assertEqual(root.terms.subject, self.principal())
        self.assertTrue(self.navigation(peer).HasField('action'))
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).HasField('action'))

    def test_private_admission_requires_persistent_enter_grant(self):
        self.enroll_second(); world = self.scoped_world()
        self.denied(self.authenticate_second(world.peers[1]), core.FORBIDDEN)
        self.grant_to_second(world, [grant.WORLD_ENTER])
        self.assertTrue(self.authenticate_second(world.peers[1]).HasField('opened'))
        self.denied(self.navigation(world.peers[1]), core.FORBIDDEN)

    def test_public_admission_does_not_create_action_grants(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS); peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.assertFalse(self.own(peer).grants)
        self.denied(self.navigation(peer), core.FORBIDDEN)

    def test_delegation_uses_authenticated_principal(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS)
        child = self.grant_to_second(world, [grant.WORLD_NAVIGATE]); peer = world.peers[1]
        self.assertEqual(child.issuer, self.principal())
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.assertTrue(self.navigation(peer).HasField('action'))
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).HasField('action'))
        self.denied(self.delegate(peer, self.root_grant(world.peers[0]), self.terms(self.second, depth=1)), core.FORBIDDEN)

    def test_invalid_delegation_consumes_actor_sequence(self):
        self.enroll_second(); world = self.scoped_world(); peer = world.peers[0]
        self.denied(self.delegate(peer, self.root_grant(peer), self.terms(self.second, depth=3)), core.FORBIDDEN)
        self.denied(peer.call(action=core.ActorActionRequest(seq=peer.action_sequence, stop=core.ActorStopRequest())), core.STALE_SEQUENCE)

    def test_grant_listing_is_subject_filtered_and_paginated(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS); peer = world.peers[1]
        one = self.grant_to_second(world, [grant.WORLD_NAVIGATE]); two = self.grant_to_second(world, [grant.WORLD_OBSERVE])
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        first = self.own(peer, limit=1); self.assertEqual(len(first.grants), 1); self.assertTrue(first.next_after)
        second = self.own(peer, limit=1, after_id=first.next_after)
        self.assertEqual({first.grants[0].id, second.grants[0].id}, {one.id, two.id}); self.assertFalse(second.next_after)
        self.assertTrue(all(record.terms.subject == self.principal(self.second) for record in (*first.grants, *second.grants)))

    def test_grant_cannot_cross_world_identity(self):
        self.enroll_second(); world = self.scoped_world(); peer = world.peers[0]
        terms = self.terms(self.second, depth=1); terms.scope.domain.id += '-another'
        self.denied(self.delegate(peer, self.root_grant(peer), terms), core.FORBIDDEN)

    def test_world_administration_does_not_mint_workspace_authority(self):
        self.enroll_second(); world = self.scoped_world(); peer = world.peers[0]
        terms = self.terms(self.second, depth=1, rights=[grant.PROCESS_EXECUTE]); terms.scope.domain.kind = grant.WORKSPACE
        self.denied(self.delegate(peer, self.root_grant(peer), terms), core.FORBIDDEN)
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/bin/true'])), core.FORBIDDEN)
        self.denied(peer.act(workspace_select=core.WorkspaceIdRequest(id='unknown')), core.FORBIDDEN)

    def test_revocation_stops_motion_without_a_recipient_request(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS); first, second = world.peers
        child = self.grant_to_second(world, [grant.WORLD_NAVIGATE])
        opened = self.authenticate_second(second).opened
        self.assertTrue(self.navigation(second).HasField('action'))
        self.assertTrue(self.revoke_grant(first, child).HasField('grant'))
        before = world.management('world.get', id=opened.actor_id)['pose']
        time.sleep(.15)
        after = world.management('world.get', id=opened.actor_id)['pose']
        self.assertEqual(before, after)
        self.denied(self.navigation(second), core.FORBIDDEN)
        self.assertTrue(second.act(stop=core.ActorStopRequest()).HasField('action'))

    def test_private_admission_revocation_closes_only_affected_session(self):
        self.enroll_second(); world = self.scoped_world(); child = self.grant_to_second(world, [grant.WORLD_ENTER, grant.WORLD_NAVIGATE])
        self.assertTrue(self.authenticate_second(world.peers[1]).HasField('opened'))
        self.assertTrue(self.revoke_grant(world.peers[0], child).HasField('grant'))
        self.denied(world.peers[1].call(inspect=session.WorldSessionInspect()), core.UNAUTHORIZED)
        self.assertTrue(self.navigation(world.peers[0]).HasField('action'))

    def test_independent_navigation_grant_survives_revocation(self):
        self.enroll_second()
        world = self.scoped_world(session.REGISTERED_ACTORS, roots=lambda:[self.terms(), self.terms(self.second, rights=[grant.WORLD_NAVIGATE], depth=0)])
        child = self.grant_to_second(world, [grant.WORLD_NAVIGATE]); peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.assertTrue(self.revoke_grant(world.peers[0], child).HasField('grant'))
        self.assertTrue(self.navigation(peer).HasField('action'))

    def test_revoked_ancestor_without_world_session_stops_descendant(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS); first, second = world.peers
        self.grant_to_second(world, [grant.WORLD_NAVIGATE]); opened = self.authenticate_second(second).opened
        first.call(close=session.WorldSessionClose())
        self.assertTrue(self.navigation(second).HasField('action'))
        self.owner_peer = self.connect(); self.revoke(kind=issuer.ACTOR_TARGET)
        time.sleep(.15); before = world.management('world.get', id=opened.actor_id)['pose']
        time.sleep(.15); self.assertEqual(before, world.management('world.get', id=opened.actor_id)['pose'])
        self.denied(self.navigation(second), core.FORBIDDEN)

    def test_grants_survive_world_restart(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS)
        child = self.grant_to_second(world, [grant.WORLD_NAVIGATE]); world.close()
        restarted = WorldProcess(self, world.config_path, world.state_path, runtime_config=world.runtime_path)
        self.worlds.append(restarted); restarted.management('system.hello')
        peer = restarted.peers[0]; self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.assertEqual(self.own(peer).grants[0].id, child.id)
        self.assertTrue(self.navigation(peer).HasField('action'))

    def test_object_scope_and_visibility_are_enforced_in_actor_engine(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS, native=True)
        one = self.object(world); two = self.object(world, 'object-two')
        world.management('world.update', id=two, expected_revision=1, patch={'pose':pose((1,0,-2))})
        self.grant_to_second(world, [grant.WORLD_OBSERVE])
        self.grant_to_second(world, [grant.WORLD_MANIPULATE], [self.world_info['entry_room'].encode(), one.encode()])
        peer = world.peers[1]; self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.denied(peer.act(object_grab=core.ObjectGrabRequest(id=two)), core.FORBIDDEN)
        self.assertTrue(peer.act(object_grab=core.ObjectGrabRequest(id=one)).HasField('action'))
        self.assertTrue(peer.act(object_release=core.ObjectReleaseRequest(commit=False)).HasField('action'))

        # Geometry follows a real authenticated edit with native visibility checks.
        owner = world.peers[0]
        before_reply = owner.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(before_reply.HasField('geometry_view'), before_reply)
        before = before_reply.geometry_view
        saved_before = world.management('world.get', id=one)
        requested = core.Pose(position=core.Vec3(x=.3, y=0, z=-2),
                              orientation=core.Quaternion(w=1))
        for operation in (
                {'object_grab': core.ObjectGrabRequest(id=one)},
                {'object_move': core.ObjectMoveRequest(pose=requested)},
                {'object_release': core.ObjectReleaseRequest(commit=True)}):
            reply = owner.act(**operation)
            self.assertTrue(reply.HasField('action'), reply)
            self.assertTrue(reply.action.accepted, reply)
        after_reply = owner.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(after_reply.HasField('geometry_view'), after_reply)
        after = after_reply.geometry_view
        before_object = next(item for item in before.objects if item.id == one)
        after_object = next(item for item in after.objects if item.id == one)
        self.assertEqual(after_object.pose, requested)
        self.assertNotEqual(after_object.pose, before_object.pose)
        self.assertGreater(after.world_revision, before.world_revision)
        self.assertEqual(after.view_id, before.view_id)
        self.assertEqual(after.authority_service_incarnation, before.authority_service_incarnation)
        self.assertEqual(after.authority_generation, before.authority_generation)
        self.assertGreater(after.sequence, before.sequence)
        saved_after = world.management('world.get', id=one)
        self.assertEqual(saved_after['pose'], pose((.3,0,-2)))
        self.assertEqual(saved_after['revision'], saved_before['revision'] + 1)
        self.assertEqual(saved_after['world_revision'], after.world_revision)

    def test_revocation_cancels_object_draft_and_releases_lease(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS, native=True); identity = self.object(world)
        self.grant_to_second(world, [grant.WORLD_OBSERVE])
        child = self.grant_to_second(world, [grant.WORLD_MANIPULATE]); first, second = world.peers
        self.assertTrue(self.authenticate_second(second).HasField('opened'))
        self.assertTrue(second.act(object_grab=core.ObjectGrabRequest(id=identity)).HasField('action'))
        self.assertTrue(self.revoke_grant(first, child).HasField('grant'))
        self.assertTrue(first.act(object_grab=core.ObjectGrabRequest(id=identity)).HasField('action'))
        self.assertTrue(first.act(stop=core.ActorStopRequest()).HasField('action'))

    def test_world_observation_omits_native_application_surfaces(self):
        world = self.scoped_world(native=True); peer = world.peers[0]; self.object(world)
        probe = Probe(self.root, xi2=True, display=os.environ['SPATIAL_TEST_DISPLAY']); self.addCleanup(probe.close)
        found = self.until(lambda:world.management('surface.find',probe_id=probe.id),lambda r:r['surface'] is not None)['surface']
        world.management('surface.place',id=found['id'],room=self.world_info['entry_room'],pose=pose((0,0,-1)),size=[2,2])
        reply = peer.act(observe=core.ActorObserveRequest()); self.assertTrue(reply.HasField('action'), reply)
        self.assertNotIn(found['id'], [item.id for item in reply.action.observation.visible])
        self.assertIn('object-one', [item.id for item in reply.action.observation.visible])
        geometry = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(geometry.HasField('geometry_view'), geometry)
        self.assertNotIn(found['id'].encode(), geometry.geometry_view.SerializeToString())
        self.assertIn('object-one', [item.id for item in geometry.geometry_view.objects])
        second = peer.act(observe=core.ActorObserveRequest())
        self.assertTrue(second.HasField('action'), second)
        self.assertNotIn(found['id'], [item.id for item in second.action.observation.visible])
        self.denied(peer.act(pointer=core.ActorPointerRequest(button=1,pressed=True)), core.FORBIDDEN)
        self.assertIsNone(probe.proc.poll())

    def test_unauthenticated_channels_cannot_list_or_change_grants(self):
        world = self.scoped_world(); peer = world.peers[1]
        self.denied(peer.call(grants=session.WorldGrantList()), core.UNAUTHORIZED)
        self.denied(peer.call(revoke=session.WorldGrantRevoke(seq=1, id=self.root_grant(world.peers[0]).id, expected_revision=1)), core.UNAUTHORIZED)

    def test_stale_revision_changes_no_grants_and_consumes_sequence(self):
        self.enroll_second(); world = self.scoped_world(); peer = world.peers[0]
        before = self.own(peer)
        self.denied(self.delegate(peer, self.root_grant(peer), self.terms(self.second, depth=1), revision=0), core.REVISION_CONFLICT)
        self.assertEqual(self.own(peer), before)
        self.denied(peer.call(action=core.ActorActionRequest(seq=peer.action_sequence, stop=core.ActorStopRequest())), core.STALE_SEQUENCE)
        self.assertTrue(self.navigation(peer).HasField('action'))

    def test_unknown_and_foreign_recipients_receive_no_grant(self):
        world = self.scoped_world(); peer = world.peers[0]; root = self.root_grant(peer)
        before = self.own(peer)
        for foreign in (False, True):
            terms = self.terms(depth=1)
            if foreign: terms.subject.issuer_id = b'F'*32
            else: terms.subject.actor_id = b'U'*32
            self.denied(self.delegate(peer, root, terms), core.FORBIDDEN)
        self.assertEqual(self.own(peer), before)

    def test_malformed_recipient_consumes_sequence_without_disconnect(self):
        world = self.scoped_world(); peer = world.peers[0]; root = self.root_grant(peer)
        terms = self.terms(depth=1); terms.subject.actor_id = b'short'
        self.denied(self.delegate(peer, root, terms), core.INVALID_ARGUMENT)
        sequence = peer.action_sequence
        self.denied(peer.call(delegate=session.WorldGrantDelegate(seq=sequence, parent_id=root.id, terms=self.terms(depth=1), expected_revision=1)), core.STALE_SEQUENCE)
        self.assertTrue(self.navigation(peer).HasField('action'))

    def test_administration_and_held_rights_do_not_imply_delegation(self):
        self.enroll_second()
        world = self.scoped_world(roots=lambda:[self.terms(depth=3, delegable=[grant.WORLD_ENTER])]); peer = world.peers[0]
        self.assertTrue(self.navigation(peer).HasField('action'))
        self.denied(self.delegate(peer, self.root_grant(peer), self.terms(self.second, rights=[grant.WORLD_NAVIGATE], depth=0)), core.FORBIDDEN)
        self.assertTrue(self.grant_to_second(world, [grant.WORLD_ENTER]).id)

    def test_seed_validation_installs_no_partial_root_grants(self):
        state = self.root/'seed-world'; initial = self.world(state=state)
        self.world_info = initial.management('world.info'); initial.close()
        directory = self.root/'invalid-authority'; directory.mkdir(mode=0o700)
        invalid = self.terms(); invalid.scope.domain.id += '-wrong'
        seed = session.WorldAuthoritySeed(protocol_version=1, authority_directory=str(directory), roots=[self.terms(), invalid])
        path = self.root/'invalid-seed.textproto'; path.write_text(text_format.MessageToString(seed)); path.chmod(0o600)
        result = subprocess.run([str(WORLD), '--state-dir', str(state), '--init-authority', str(path)], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 2)
        with sqlite3.connect(directory/'authority.db') as database:
            self.assertEqual(database.execute('SELECT count(*) FROM grant_record').fetchone()[0], 0)
            self.assertEqual(database.execute('SELECT revision FROM authority').fetchone()[0], 0)

    def test_missing_authority_store_does_not_initialize_implicitly(self):
        world = self.scoped_world(); world.close()
        config = session.WorldIdentityConfig(); text_format.Parse(world.config_path.read_text(), config)
        missing = self.root/'uninitialized'; missing.mkdir(mode=0o700); config.authority_directory = str(missing)
        world.config_path.write_text(text_format.MessageToString(config))
        restarted = WorldProcess(self, world.config_path, world.state_path); self.worlds.append(restarted)
        self.assertEqual(restarted.proc.wait(timeout=5), 2)
        self.assertFalse((missing/'authority.db').exists())

    def test_persistent_config_rejects_static_authority_and_wrong_world(self):
        world = self.scoped_world(); world.close()
        config = session.WorldIdentityConfig(); text_format.Parse(world.config_path.read_text(), config)
        config.actor_grants.add(actor_id=self.identity.actor_id, grants=['interact'])
        world.config_path.write_text(text_format.MessageToString(config))
        mixed = WorldProcess(self, world.config_path, world.state_path); self.worlds.append(mixed)
        self.assertEqual(mixed.proc.wait(timeout=5), 2)
        config.ClearField('actor_grants'); world.config_path.write_text(text_format.MessageToString(config))
        wrong = WorldProcess(self, world.config_path, self.root/'another-world'); self.worlds.append(wrong)
        self.assertEqual(wrong.proc.wait(timeout=5), 2)

    def test_ancestor_credential_revocation_preserves_delegated_actor_rights(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS)
        self.grant_to_second(world, [grant.WORLD_NAVIGATE]); peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.owner_peer = self.connect(); self.revoke(kind=issuer.CREDENTIAL_TARGET)
        self.assertTrue(self.navigation(peer).HasField('action'))

    def test_observation_revocation_cancels_object_lease(self):
        self.enroll_second(); world = self.scoped_world(session.REGISTERED_ACTORS, native=True); identity = self.object(world)
        observation = self.grant_to_second(world, [grant.WORLD_OBSERVE])
        self.grant_to_second(world, [grant.WORLD_MANIPULATE]); first, second = world.peers
        self.assertTrue(self.authenticate_second(second).HasField('opened'))
        self.assertTrue(second.act(object_grab=core.ObjectGrabRequest(id=identity)).HasField('action'))
        self.assertTrue(self.revoke_grant(first, observation).HasField('grant'))
        self.denied(second.act(observe=core.ActorObserveRequest()), core.FORBIDDEN)
        self.assertTrue(first.act(object_grab=core.ObjectGrabRequest(id=identity)).HasField('action'))

    def test_credential_revocation_during_ancestor_preparation_prevents_entry(self):
        def revoke():
            self.owner_peer = self.connect(); self.revoke(kind=issuer.CREDENTIAL_TARGET)
        relay = RelayingIssuer(self, revoke)
        world = self.scoped_world(issuer_port=relay.port, authenticate=False)
        relay.armed.set()
        self.denied(self.authenticate(world.peers[0]), core.UNAUTHORIZED)
        self.assertTrue(relay.delivered.is_set())
        self.assertFalse([entity for entity in world.management('world.list')['entities'] if entity['kind'] == 'actor'])


if __name__ == '__main__': unittest.main(verbosity=2)
