"""Focused geometry transport cases against real issuer, participant, and worldd."""
import unittest
import socket
import struct
import time
import select

import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.oracles import pose
from harness.wire import frame
from test_world_authority import WorldAuthorityTests
from test_world_remote import RemoteWorldTests


class GeometryViewTests(WorldAuthorityTests):
    def geometry(self, peer):
        reply = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(reply.HasField('geometry_view'), reply)
        return reply.geometry_view

    def test_geometry_local_headless_context_and_privacy(self):
        world = self.scoped_world(native=False)
        peer = world.peers[0]
        opened = peer.call(inspect=session.WorldSessionInspect()).state
        room = self.world_info['entry_room']
        world.management('world.create', entity={'id':'visible-object', 'kind':'object', 'room':room,
            'pose':pose((0,0,-2))})
        other = world.management('world.create', entity={'id':'other-room', 'kind':'room', 'label':'Other'})
        world.management('world.create', entity={'id':'privacy-portal', 'kind':'portal', 'room':room,
            'pose':pose((1,0,-2)), 'size':[2,3],
            'destination':{'room':other['id'], 'pose':pose((9,0,9))}})
        world.management('world.create', entity={'id':'hidden-object', 'kind':'object', 'room':other['id'],
            'pose':pose((0,0,-2))})
        view = self.geometry(peer)
        self.assertEqual(view.world_id, self.world_info['world_id'])
        self.assertEqual(view.actor_id, opened.id)
        self.assertEqual(view.room, room)
        self.assertEqual(len(view.view_id), 32)
        self.assertEqual(len(view.authority_service_incarnation), 32)
        self.assertGreater(view.authority_generation, 0)
        self.assertEqual([item.id for item in view.objects], ['visible-object'])
        self.assertEqual([item.id for item in view.portals], ['privacy-portal'])
        self.assertNotIn(b'hidden-object', view.SerializeToString())
        self.assertNotIn(other['id'].encode(), view.SerializeToString())
        self.assertNotIn(b'workspace', view.SerializeToString())
        self.assertNotIn(b'application', view.SerializeToString())
        self.assertFalse({'label', 'destination', 'workspace', 'application', 'surface', 'pixels', 'xid'}
                         & set(view.DESCRIPTOR.fields_by_name))
        self.assertEqual(self.geometry(peer).sequence, view.sequence + 1)

    def test_geometry_entry_is_not_observation(self):
        self.enroll_second()
        world = self.scoped_world(session.REGISTERED_ACTORS)
        self.grant_to_second(world, [grants.WORLD_ENTER])
        peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        self.denied(peer.call(geometry_view=session.WorldGeometryViewRequest()), core.FORBIDDEN)
        self.assertTrue(peer.call(inspect=session.WorldSessionInspect()).HasField('state'))
        self.assertTrue(self.geometry(world.peers[0]).sequence)

    def test_geometry_revocation_and_preparation(self):
        self.enroll_second()
        world = self.scoped_world(session.REGISTERED_ACTORS)
        child = self.grant_to_second(world, [grants.WORLD_ENTER, grants.WORLD_OBSERVE])
        peer = world.peers[1]
        prepared = self.authenticate(peer, self.second_record, self.second_key,
                                     self.second_public, transition_id=b't'*32)
        self.assertTrue(prepared.HasField('prepared'))
        self.assertFalse(prepared.prepared.actor.geometry_view_id)
        self.denied(peer.call(geometry_view=session.WorldGeometryViewRequest()), core.UNAUTHORIZED)
        active = peer.call(activate=session.WorldSessionActivate(transition_id=b't'*32))
        self.assertTrue(active.HasField('opened'))
        self.assertEqual(self.geometry(peer).view_id, active.opened.geometry_view_id)
        self.assertTrue(self.revoke_grant(world.peers[0], child).HasField('grant'))
        denied = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.denied(denied, core.FORBIDDEN)
        self.assertFalse(denied.HasField('geometry_view'), denied)
        inspected = peer.call(inspect=session.WorldSessionInspect())
        self.assertTrue(inspected.HasField('state'), inspected)
        self.assertTrue(self.geometry(world.peers[0]).sequence)

    def test_geometry_second_actor_motion(self):
        self.enroll_second()
        world = self.scoped_world(session.REGISTERED_ACTORS)
        room = self.world_info['entry_room']
        self.object(world)
        self.grant_to_second(world, [grants.WORLD_ENTER, grants.WORLD_NAVIGATE])
        peer = world.peers[1]
        second = self.authenticate_second(peer).opened.actor_id
        first = world.peers[0]
        before = self.geometry(first)
        self.assertIn(second, [item.id for item in before.actors])
        self.assertTrue(peer.act(input=core.ActorInputRequest(axes=[1,0,0,0,0,0])).HasField('action'))
        moved = self.until(lambda:self.geometry(first),
            lambda v: next(item.pose for item in v.actors if item.id == second) !=
                      next(item.pose for item in before.actors if item.id == second))
        self.assertEqual(moved.world_revision, before.world_revision)
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).HasField('action'))

    def test_geometry_departure_and_credential_revocation(self):
        world = self.scoped_world(native=False)
        peer = world.peers[0]
        self.assertTrue(self.geometry(peer).sequence)
        departed = peer.call(depart=session.WorldSessionDepart(seq=1, transition_id=b'd'*32))
        self.assertTrue(departed.HasField('departed'))
        self.denied(peer.call(geometry_view=session.WorldGeometryViewRequest()), core.UNAUTHORIZED)
        other = world.peers[1]
        self.assertTrue(self.authenticate(other).HasField('opened'))
        self.assertTrue(self.geometry(other).sequence)
        self.owner_peer = self.connect()
        self.revoke(kind=issuer.CREDENTIAL_TARGET)
        self.denied(other.call(geometry_view=session.WorldGeometryViewRequest()), core.UNAUTHORIZED)

    def test_geometry_queued_output_revocation_cancels_only_observer(self):
        self.enroll_second()
        world = self.scoped_world(session.REGISTERED_ACTORS)
        owner, observer = world.peers
        grant = self.grant_to_second(world, [grants.WORLD_ENTER, grants.WORLD_OBSERVE])
        self.assertTrue(self.authenticate_second(observer).HasField('opened'))
        room = self.world_info['entry_room']
        for index in range(1500):
            identity = f'geometry-{index:04d}-' + 'x' * 220
            world.management('world.create', entity={'id':identity, 'kind':'object', 'room':room,
                'pose':pose((0,0,-2))})
        observer.stream.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        observer.sequence += 1
        request = session.WorldSessionRequest(protocol_version=1, request_id=observer.sequence,
            geometry_view=session.WorldGeometryViewRequest())
        observer.stream.sendall(frame(request))
        # Observe a real, incomplete queued frame without consuming its payload.
        # Revocation before dispatch would test denial, not queued-output cancellation.
        deadline = time.monotonic() + 5
        prefix = b''
        while len(prefix) < 4:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, 'Geometry response header was not queued before revocation')
            self.assertTrue(select.select([observer.stream], [], [], remaining)[0],
                            'Geometry response was not queued before revocation')
            prefix = observer.stream.recv(65536, socket.MSG_PEEK | socket.MSG_DONTWAIT)
            self.assertTrue(prefix, 'Observer closed before geometry was queued')
            if len(prefix) < 4:
                time.sleep(.001)
        declared = struct.unpack('!I', prefix[:4])[0]
        self.assertGreater(declared, 300000, 'Queued response is not the large geometry view')
        self.assertLessEqual(declared, 1048576)
        self.assertGreater(len(prefix), 4, 'Queued geometry payload prefix missing')
        self.assertLess(len(prefix) - 4, declared, 'Peek consumed or returned a completed view')
        self.assertTrue(self.revoke_grant(owner, grant).HasField('grant'))
        observer.stream.settimeout(5)
        received = bytearray()
        while True:
            chunk = observer.stream.recv(65536)
            if not chunk:
                break
            received.extend(chunk)
            self.assertLess(len(received), 1048580, 'Unauthorized completed geometry frame')
        if len(received) >= 4:
            declared = struct.unpack('!I', received[:4])[0]
            self.assertLess(len(received) - 4, declared, 'Revoked observer received a complete view')
        self.assertTrue(self.geometry(owner).sequence)

    def test_geometry_count_limit_is_controlled_and_other_actor_survives(self):
        self.enroll_second()
        world = self.scoped_world(session.REGISTERED_ACTORS)
        self.grant_to_second(world, [grants.WORLD_ENTER, grants.WORLD_OBSERVE])
        peer = world.peers[1]
        self.assertTrue(self.authenticate_second(peer).HasField('opened'))
        room = self.world_info['entry_room']
        for index in range(4097):
            world.management('world.create', entity={'id':f'count-{index:04d}', 'kind':'object',
                'room':room, 'pose':pose((0,0,-2))})
        self.denied(peer.call(geometry_view=session.WorldGeometryViewRequest()), core.INVALID_ARGUMENT)
        self.assertTrue(world.peers[0].call(inspect=session.WorldSessionInspect()).HasField('state'))

    def test_geometry_oversized_world_snapshot_is_controlled(self):
        world = self.scoped_world(native=False)
        peer = world.peers[0]
        room = self.world_info['entry_room']
        for index in range(4000):
            identity = f'large-{index:04d}-' + 'x' * 245
            world.management('world.create', entity={'id':identity, 'kind':'object', 'room':room,
                'pose':pose((0,0,-2))})
        denied = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.denied(denied, core.RESOURCE_EXHAUSTED)
        self.assertEqual(denied.error.message,
                         'Snapshot exceeds frame limit; no partial snapshot returned')
        self.assertFalse(denied.HasField('geometry_view'), denied)
        inspected = peer.call(inspect=session.WorldSessionInspect())
        self.assertTrue(inspected.HasField('state'), inspected)


class GeometryTlsTests(RemoteWorldTests):
    def test_geometry_authenticated_tls_headless(self):
        world = self.remote_world()
        peer = world.connect(self)
        self.assertEqual(peer.stream.version(), 'TLSv1.3')
        opened = self.authenticate(peer).opened
        reply = peer.call(geometry_view=session.WorldGeometryViewRequest())
        self.assertTrue(reply.HasField('geometry_view'), reply)
        view = reply.geometry_view
        self.assertEqual(view.actor_id, opened.actor_id)
        self.assertEqual(view.world_id, opened.world_id)
        self.assertEqual(view.view_id, opened.geometry_view_id)
        self.assertEqual(view.authority_service_incarnation, opened.authority_service_incarnation)
        self.assertEqual(view.authority_generation, opened.authority_generation)
        self.assertEqual(view.room, self.world_info['entry_room'])
        self.assertFalse(view.panels)


if __name__ == '__main__':
    names = ('test_geometry_local_headless_context_and_privacy', 'test_geometry_entry_is_not_observation',
             'test_geometry_revocation_and_preparation', 'test_geometry_second_actor_motion',
             'test_geometry_departure_and_credential_revocation',
             'test_geometry_queued_output_revocation_cancels_only_observer',
             'test_geometry_count_limit_is_controlled_and_other_actor_survives',
             'test_geometry_oversized_world_snapshot_is_controlled')
    tests = [GeometryViewTests(name) for name in names]
    tests.append(GeometryTlsTests('test_geometry_authenticated_tls_headless'))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(tests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
