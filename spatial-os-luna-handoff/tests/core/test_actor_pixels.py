"""Real filtered pixels through authenticated actor sessions and native C clients."""
import os
import select
import socket
import struct
import threading
import unittest
from unittest.mock import patch
import test_world_application_control as control
import test_world_authority as world_authority
import issuer_pb2 as issuer
from harness.wire import frame
from harness.oracles import pose
from test_world_identity import receive

core = control.core
grant = control.grant
session = control.session
observation = control.observation


class ActorPixelTests(observation.ApplicationObservationTests):
    probe = control.WorldApplicationControlTests.probe
    probe_events = control.WorldApplicationControlTests.probe_events
    probe_command = control.WorldApplicationControlTests.probe_command

    def pixels(self, peer=None, width=127, height=95):
        request = core.ActorObserveRequest(include_pixels=True)
        if width is not None:
            request.width = width
            request.height = height
        reply = (peer or self.viewer).act(observe=request)
        self.assertTrue(reply.HasField('action'), reply)
        value = reply.action.observation
        self.assertTrue(value.HasField('image'), 'Requested real pixel image is missing')
        self.assertEqual((value.image.width, value.image.height), (value.viewport.width, value.viewport.height))
        self.assertEqual(len(value.image.rgb8), value.image.width * value.image.height * 3)
        self.assertGreater(value.frame_id, 0)
        return value

    def rgb(self, observation, x, y):
        start = (y * observation.image.width + x) * 3
        return tuple(observation.image.rgb8[start:start + 3])

    def setup_pixels(self):
        self.setup_world()
        app, surface = self.probe()
        self.grant_viewer([grant.WORKSPACE_ACCESS])
        record = self.grant_viewer([grant.APPLICATION_OBSERVE], app)
        return app, surface, record

    def test_authorized_pixels_have_native_quadrants_and_top_left_rows(self):
        app, surface, record = self.setup_pixels()
        value = self.pixels()
        for x, y, rgb in [(58, 42, (255, 0, 0)), (68, 42, (0, 255, 0)),
                          (58, 52, (0, 0, 255)), (68, 52, (255, 255, 255))]:
            self.assertEqual(self.rgb(value, x, y), rgb)
        self.assertIn(surface['id'], {v.id for v in value.visible})
        self.assertFalse(self.viewer.call(inspect=session.WorldSessionInspect()).state.workspace)

    def test_ungranted_application_pixels_match_empty_world(self):
        self.setup_world()
        empty = self.pixels()
        app, surface = self.probe()
        self.grant_viewer([grant.WORKSPACE_ACCESS])
        denied = self.pixels()
        self.assertEqual(denied.image.rgb8, empty.image.rgb8)
        self.assertNotIn(surface['id'], {v.id for v in denied.visible})
        self.assertNotEqual(self.pixels(self.peer).image.rgb8, empty.image.rgb8)

    def test_observation_grant_without_workspace_access_has_no_application_pixels(self):
        self.setup_world()
        empty = self.pixels()
        app, surface = self.probe()
        self.grant_viewer([grant.APPLICATION_OBSERVE], app)
        self.assertEqual(self.pixels().image.rgb8, empty.image.rgb8)

    def test_live_paint_changes_the_next_pixel_observation(self):
        app, surface, record = self.setup_pixels()
        first = self.pixels()
        for color in [(17, 61, 211), (197, 83, 29)]:
            self.probe_command(app, 'paint', *color)
            value = self.until(self.pixels, lambda v: self.rgb(v, 63, 47) == color)
            self.assertGreater(value.frame_id, first.frame_id)
            item = next(v for v in value.visible if v.id == surface['id'])
            self.assertEqual(self.rgb(value, item.sample.x, item.sample.y), color)
            self.assertEqual(item.visible_pixels, sum(value.image.rgb8[i:i+3] == bytes(color)
                for i in range(0, len(value.image.rgb8), 3)))

    def test_revoked_pixels_disappear_without_terminating_application(self):
        self.setup_world()
        empty = self.pixels()
        app, surface = self.probe()
        self.grant_viewer([grant.WORKSPACE_ACCESS])
        record = self.grant_viewer([grant.APPLICATION_OBSERVE], app)
        self.assertNotEqual(self.pixels().image.rgb8, empty.image.rgb8)
        self.revoke_grant(self.peer, record)
        self.assertEqual(self.pixels().image.rgb8, empty.image.rgb8)
        self.assertTrue(self.world_process.management('surface.get', id=surface['id'])['mapped'])
        self.assertNotEqual(self.pixels(self.peer).image.rgb8, empty.image.rgb8)

    def test_other_application_scope_does_not_export_its_pixels(self):
        self.setup_world()
        app, left = self.probe((-.6, 0, -2))
        other, right = self.probe((.6, 0, -2))
        self.probe_command(app, 'paint', 17, 61, 211)
        self.probe_command(other, 'paint', 197, 83, 29)
        self.grant_viewer([grant.WORKSPACE_ACCESS])
        self.grant_viewer([grant.APPLICATION_OBSERVE], app)
        value = self.pixels()
        colors = {value.image.rgb8[i:i+3] for i in range(0, len(value.image.rgb8), 3)}
        self.assertIn(bytes((17, 61, 211)), colors)
        self.assertNotIn(bytes((197, 83, 29)), colors)
        self.assertEqual({v.id for v in value.visible}, {left['id']})

    def test_resize_and_unmap_update_image_and_surface_metadata(self):
        app, surface, record = self.setup_pixels()
        self.pixels()
        self.probe_command(app, 'resize', 640, 480)
        value = self.until(self.pixels, lambda v: any(x.id == surface['id'] and x.pixels.width == 640 for x in v.visible))
        self.assertEqual(self.rgb(value, 58, 42), (255, 0, 0))
        self.probe_command(app, 'unmap')
        hidden = self.until(self.pixels, lambda v: not v.visible)
        self.assertEqual(len(set(hidden.image.rgb8[i:i+3] for i in range(0, len(hidden.image.rgb8), 3))), 1)

    def test_other_room_and_occluded_application_pixels_are_absent(self):
        app, surface, record = self.setup_pixels()
        self.pixels()
        room = self.world_process.management('world.create', entity=dict(id='pixel-private-room', kind='room', label='private'))
        self.world_process.management('surface.place', id=surface['id'], room=room['id'], pose=pose((0, 0, -2)), size=[1, 1])
        hidden = self.pixels()
        self.assertFalse(hidden.visible)
        self.world_process.management('surface.place', id=surface['id'], room=self.world_info['entry_room'], pose=pose((0, 0, -2)), size=[1, 1])
        self.world_process.management('world.create', entity=dict(id='pixel-cover', kind='panel', label='occluding panel', room=self.world_info['entry_room'],
            pose=pose((0, 0, -1)), size=[2, 2], color=[71, 131, 193]))
        covered = self.pixels()
        self.assertNotIn(surface['id'], {v.id for v in covered.visible})
        self.assertEqual(self.rgb(covered, 63, 47), (71, 131, 193))

    def test_default_pixel_viewport_has_no_capture_file_or_input_authority(self):
        app, surface, record = self.setup_pixels()
        before = set(self.root.rglob('frame-*.ppm'))
        value = self.pixels(width=None)
        self.assertEqual((value.image.width, value.image.height), (512, 512))
        self.assertEqual(set(self.root.rglob('frame-*.ppm')), before)
        self.viewer.act(aim=core.ActorAimRequest(direction=core.Vec3(z=-1)))
        self.denied(self.viewer.act(pointer=core.ActorPointerRequest(button=1, pressed=True)), core.FORBIDDEN)
        self.assertFalse(self.viewer.call(inspect=session.WorldSessionInspect()).state.HasField('focus'))

    def test_metadata_only_requests_have_no_image_and_keep_larger_viewports(self):
        app, surface, record = self.setup_pixels()
        for request in [core.ActorObserveRequest(), core.ActorObserveRequest(width=1024, height=512, include_pixels=False)]:
            reply = self.viewer.act(observe=request)
            self.assertTrue(reply.HasField('action'), reply)
            self.assertFalse(reply.action.observation.HasField('image'))
            self.assertIn(surface['id'], {v.id for v in reply.action.observation.visible})

    def test_excessive_pixel_viewport_is_rejected_without_changing_world(self):
        self.setup_world()
        before = self.world_process.management('world.snapshot')
        for width, height in [(513, 512), (4096, 65), (0, 10)]:
            self.denied(self.viewer.act(observe=core.ActorObserveRequest(width=width, height=height, include_pixels=True)), core.INVALID_ARGUMENT)
        self.assertEqual(self.world_process.management('world.snapshot'), before)

    def test_revocation_before_post_render_check_returns_no_pixels(self):
        app, surface, record = self.setup_pixels()
        def revoke(count, reply):
            if count == 1:
                control.access.ApplicationAccessTests.revoke_record(self, record)
        self.relay.calls = 0
        self.relay.hook = revoke
        self.denied(self.viewer.act(observe=core.ActorObserveRequest(width=127, height=95, include_pixels=True)), core.FORBIDDEN)

    def test_revocation_before_first_queued_write_exports_no_pixel_reply(self):
        app, surface, record = self.setup_pixels()
        def revoke(count, reply):
            if count == 2:
                control.access.ApplicationAccessTests.revoke_record(self, record)
        self.relay.calls = 0
        self.relay.hook = revoke
        try:
            reply = self.viewer.act(observe=core.ActorObserveRequest(width=127, height=95, include_pixels=True))
        except EOFError:
            pass
        else:
            self.fail(f'Expected queued-write channel closure; received {reply}')
        self.assertGreaterEqual(self.relay.calls, 3)

    def test_revocation_stops_a_partially_written_pixel_reply(self):
        original_pair = socket.socketpair
        def bounded_pair(*args, **kwargs):
            left, right = original_pair(*args, **kwargs)
            right.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            return left, right
        with patch.object(socket, 'socketpair', bounded_pair):
            app, surface, record = self.setup_pixels()
        self.pixels()  # Establish real pixel support before testing a partial export.
        self.viewer.sequence += 1
        self.viewer.action_sequence += 1
        request = session.WorldSessionRequest(protocol_version=1, request_id=self.viewer.sequence,
            action=core.ActorActionRequest(seq=self.viewer.action_sequence,
                observe=core.ActorObserveRequest(width=512, height=512, include_pixels=True)))
        self.viewer.stream.sendall(frame(request))
        self.assertTrue(select.select([self.viewer.stream], [], [], 3)[0])
        self.revoke_grant(self.peer, record)
        with self.assertRaises(EOFError):
            receive(self.viewer.stream.recv, self.viewer.stream, session.WorldSessionReply)
        self.assertTrue(self.world_process.management('surface.get', id=surface['id'])['mapped'])



class PresentationPixelFenceTests(ActorPixelTests):
    def terms(self, claims=None, rights=None, depth=3, components=(), delegable=None):
        rights = [*world_authority.RIGHTS, grant.WORLD_PLACEMENT_CREATE,
                  grant.REPRESENTATION_VIEW] if rights is None else rights
        components = tuple(value.encode('utf-8') if isinstance(value, str) else value
                           for value in components)
        terms = super().terms(claims, rights, depth, components, delegable)
        self.assertIsInstance(terms.scope.domain.id, str)
        self.assertTrue(all(isinstance(value, bytes) for value in terms.scope.components))
        return terms

    def setup_world(self, rights=None, legacy=False):
        rights = [*control.access.ALL, grant.WORKSPACE_PRESENTATION_ATTACH] if rights is None else rights
        return super().setup_world(rights=rights, legacy=legacy)

    def setUp(self):
        super().setUp()
        self.presentation_relay = world_authority.RelayingIssuer(self, self.revoke_second_actor)
        self.presentation_relay.trigger_after_status = 1

    def config_file(self, endpoint_port=None, **overrides):
        return super().config_file(endpoint_port or self.presentation_relay.port, **overrides)

    def revoke_second_actor(self):
        target = issuer.IdentityReference(kind=issuer.ACTOR_TARGET, id=self.second.actor_id)
        operation = issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(
            target=target, expected_security_revision=0, reason='presentation observation race'))
        owner = self.connect()
        try:
            reply = self.execute(owner, self.challenge(owner, operation))
        finally:
            owner.close()
        self.assertTrue(reply.HasField('identity_status'), reply)
        self.assertEqual(reply.identity_status.actor_id, self.second.actor_id)
        self.assertTrue(reply.identity_status.actor_revoked)

    def test_pixels_are_withheld_when_later_presentation_check_revokes_actor(self):
        app, surface, _ = self.setup_pixels()
        self.grant_viewer([grant.WORKSPACE_PRESENTATION_ATTACH])
        entry = self.world_info['entry_room']
        response = self.peer.call(grants=session.WorldGrantList())
        self.assertTrue(response.HasField('grants'), response)
        page = response.grants
        root = next(item for item in page.grants if not item.parent_id)
        terms = self.terms(self.second,
            rights=[grant.WORLD_PLACEMENT_CREATE, grant.REPRESENTATION_VIEW],
            depth=0, components=[entry])
        self.peer.action_sequence += 1
        delegated = self.peer.call(delegate=session.WorldGrantDelegate(
            seq=self.peer.action_sequence, parent_id=root.id, terms=terms,
            expected_revision=page.revision))
        self.assertTrue(delegated.HasField('grant'), delegated)
        created = self.viewer.act(presentation_create=core.PresentationCreateRequest(
            protocol_version=1, operation_id=os.urandom(16), workspace_id='one', room_id=entry,
            transform=pose((.8, 0, -2)),
            expected_world_revision=self.world_process.management('world.info')['world_revision']))
        self.assertTrue(created.HasField('action'), created)
        baseline = self.pixels()
        self.assertTrue(baseline.HasField('image'), 'real application pixels are required for this control')
        self.assertEqual(len(baseline.presentation_views), 1)
        self.assertEqual(baseline.presentation_views[0].representation_id,
            created.action.presentation.representation_id)

        post_render_access = threading.Event()
        def arm_after_post_render_access(count, reply):
            if count == 2:
                self.assertTrue(reply.HasField('application_access'), reply)
                decisions = reply.application_access.applications
                self.assertTrue(decisions and decisions[0].observe,
                    'post-render application observation was not authorized')
                post_render_access.set()
                self.presentation_relay.armed.set()
        self.relay.calls = 0
        self.relay.hook = arm_after_post_render_access
        raced = self.viewer.act(observe=core.ActorObserveRequest(
            width=127, height=95, include_pixels=True))
        self.assertTrue(post_render_access.is_set(),
            'post-render application authorization did not complete before revocation')
        self.assertEqual(self.relay.calls, 2,
            'expected scene and post-render application authorization replies')
        self.assertTrue(self.presentation_relay.triggered.wait(.5),
            'subsequent presentation workspace check did not trigger issuer revocation')
        with self.presentation_relay.status_lock:
            self.assertEqual(self.presentation_relay.status_seen, 1)
        self.assertTrue(raced.HasField('error'), raced)
        self.assertIn(raced.error.code, (core.FORBIDDEN, core.UNAVAILABLE), raced)
        self.assertFalse(raced.HasField('action'), 'revoked observation must return no pixel payload')
        self.assertTrue(self.presentation_relay.delivered.is_set())
        self.assertFalse(self.presentation_relay.errors)

def suite():
    tests = [ActorPixelTests(name) for name in vars(ActorPixelTests) if name.startswith('test_')]
    tests.append(PresentationPixelFenceTests('test_pixels_are_withheld_when_later_presentation_check_revokes_actor'))
    return unittest.TestSuite(tests)


if __name__ == '__main__':
    unittest.main(defaultTest='suite', verbosity=2)
