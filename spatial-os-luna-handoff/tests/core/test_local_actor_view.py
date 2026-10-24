"""Retained local attestation uses real TLS actors, broker rights, and native witnesses."""
import os
import unittest
from unittest.mock import patch
import actor_local_view_pb2 as local_view
import core_pb2 as core
import world_session_pb2 as session
import test_world_authority as authority
import test_world_remote_native as remote_fixture
import test_world_application_control as control
from harness.wire import frame
from test_world_identity import receive

grant = control.grant


class LocalActorViewTests(remote_fixture.RemoteNativeTests):
    def local_context(self, *, workspace=True, observe=True):
        with patch.object(authority, 'WorldProcess', remote_fixture.HybridWorldProcess):
            self.setup_world()
        empty = self.pixels()
        app, surface = self.probe()
        if workspace:
            self.grant_viewer([grant.WORKSPACE_ACCESS])
        observation = self.grant_viewer([grant.APPLICATION_OBSERVE], app) if observe else None
        self.assertTrue(self.viewer.call(close=session.WorldSessionClose()).HasField('closed'))
        remote = self.world_process.connect(self)
        opened = self.authenticate_second(remote)
        self.assertTrue(opened.HasField('opened'), opened)
        value = opened.opened
        identity = local_view.ActorLocalViewIdentity(protocol_version=1, world_id=value.world_id,
            actor_id=value.actor_id, view_id=value.geometry_view_id,
            authority_service_incarnation=value.authority_service_incarnation,
            authority_generation=value.authority_generation)
        self.assertEqual(len(identity.view_id), 32)
        return remote, identity, empty, app, surface, observation

    def retained_view(self, identity, operation='bind'):
        request = core.Request(protocol_version=1)
        request.actor_local_view.protocol_version = 1
        request.actor_local_view.identity.CopyFrom(identity)
        getattr(request.actor_local_view, operation).SetInParent()
        self.world_process.sequence += 1
        request.request_id = self.world_process.sequence
        self.world_process.proc.stdin.write(frame(request))
        self.world_process.proc.stdin.flush()
        reply = receive(lambda size: os.read(self.world_process.proc.stdout.fileno(), size),
            self.world_process.proc.stdout, core.Reply)
        self.assertEqual((reply.protocol_version, reply.request_id), (1, request.request_id))
        return reply

    def attest(self, identity):
        reply = self.retained_view(identity)
        self.assertTrue(reply.HasField('actor_local_view'), reply)
        self.assertEqual(reply.actor_local_view.identity, identity)
        self.assertTrue(reply.actor_local_view.bound)
        self.assertFalse(reply.actor_local_view.presented)
        return reply.actor_local_view

    def test_public_actor_cannot_invoke_retained_attestation(self):
        remote, identity, empty, app, surface, _ = self.local_context()
        self.assertEqual(self.pixels(remote).image.rgb8, empty.image.rgb8)
        request = core.Request(protocol_version=1, actor_local_view=core.ActorLocalViewRequest(
            protocol_version=1, identity=identity, bind=core.ActorLocalViewBind()))
        denied = remote.request(request)
        self.assertTrue(denied.HasField('error'), denied)
        self.assertIn(denied.error.code, (core.INVALID_ARGUMENT, core.UNKNOWN_METHOD))
        self.assertEqual(self.pixels(remote).image.rgb8, empty.image.rgb8)
        pointer = remote.act(pointer=core.ActorPointerRequest(button=1, pressed=True))
        self.assertEqual(pointer.error.code, core.UNIMPLEMENTED, pointer)
        self.assertTrue(self.world_process.management('surface.get', id=surface['id'])['mapped'])

    def test_exact_binding_enables_real_filtered_pixels_without_workspace_selection(self):
        remote, identity, empty, app, surface, _ = self.local_context()
        before = remote.call(inspect=session.WorldSessionInspect()).state
        self.assertFalse(before.workspace)
        self.assertEqual(self.pixels(remote).image.rgb8, empty.image.rgb8)
        self.attest(identity)
        visible = self.pixels(remote)
        self.assertIn(surface['id'], {item.id for item in visible.visible})
        self.assertEqual(self.rgb(visible, 58, 42), (255, 0, 0))
        after = remote.call(inspect=session.WorldSessionInspect()).state
        self.assertEqual(after, before)
        self.attest(identity)  # Idempotent binding does not consume an actor sequence or replace its identity.
        self.assertEqual(self.pixels(remote).image.rgb8, visible.image.rgb8)

    def test_binding_does_not_grant_workspace_access(self):
        remote, identity, empty, app, surface, _ = self.local_context(workspace=False)
        self.attest(identity)
        self.assertEqual(self.pixels(remote).image.rgb8, empty.image.rgb8)
        self.grant_viewer([grant.WORKSPACE_ACCESS])
        self.assertIn(surface['id'], {item.id for item in self.pixels(remote).visible})

    def test_binding_does_not_grant_or_preserve_revoked_application_observation(self):
        remote, identity, empty, app, surface, _ = self.local_context(observe=False)
        self.attest(identity)
        self.assertEqual(self.pixels(remote).image.rgb8, empty.image.rgb8)
        record = self.grant_viewer([grant.APPLICATION_OBSERVE], app)
        self.assertIn(surface['id'], {item.id for item in self.pixels(remote).visible})
        self.revoke_grant(self.peer, record)
        self.assertEqual(self.pixels(remote).image.rgb8, empty.image.rgb8)
        self.assertTrue(self.world_process.management('surface.get', id=surface['id'])['mapped'])
        self.probe_command(app, 'sync')

    def test_forged_session_and_authority_bindings_reject_before_enabling_output(self):
        remote, identity, empty, app, surface, _ = self.local_context()
        replacements = dict(world_id='foreign-world', actor_id='foreign-actor', view_id=b'v' * 32,
            authority_service_incarnation=b'i' * 32, authority_generation=identity.authority_generation + 1)
        for field, value in replacements.items():
            with self.subTest(field=field):
                forged = local_view.ActorLocalViewIdentity()
                forged.CopyFrom(identity)
                setattr(forged, field, value)
                denied = self.retained_view(forged)
                self.assertTrue(denied.HasField('error'), denied)
                self.assertEqual(denied.error.code, core.FORBIDDEN, denied)
                self.assertEqual(self.pixels(remote).image.rgb8, empty.image.rgb8)
        self.attest(identity)
        self.assertIn(surface['id'], {item.id for item in self.pixels(remote).visible})

    def test_release_cancels_held_native_input_and_preserves_application(self):
        remote, identity, empty, app, surface, _ = self.local_context()
        self.grant_viewer([grant.WORKSPACE_ACCESS, *control.access.workspace.PROJECT])
        self.attest(identity)
        aim = self.actor_aim(remote)
        self.assertEqual(aim.hit.id, surface['id'])
        denied = remote.act(pointer=core.ActorPointerRequest(button=1, pressed=True))
        self.assertEqual(denied.error.code, core.FORBIDDEN, denied)
        self.assertIn('control', denied.error.message.lower())
        self.grant_viewer([grant.APPLICATION_CONTROL], app)
        self.actor_aim(remote)
        self.actor_pointer(True, remote)
        self.send_key_event('F5', True, remote)
        self.probe_command(app, 'sync')
        rows = self.probe_events(app)
        self.assertTrue(any(row.get('event') == 'button' and row.get('pressed') for row in rows))
        self.assertTrue(any(row.get('event') == 'key' and row.get('pressed') for row in rows))
        released = self.retained_view(identity, 'release')
        self.assertTrue(released.HasField('actor_local_view'), released)
        self.assertFalse(released.actor_local_view.bound)
        self.probe_command(app, 'sync')
        rows = self.probe_events(app)
        for event in ('key', 'button'):
            self.assertEqual(sum(row.get('event') == event and row.get('pressed') is True for row in rows),
                sum(row.get('event') == event and row.get('pressed') is False for row in rows), event)
        self.assertEqual(sum(row.get('event') == 'ready' and not row.get('xi2') for row in rows), 1)
        self.assertEqual(self.pixels(remote).image.rgb8, empty.image.rgb8)
        current = self.world_process.management('surface.get', id=surface['id'])
        self.assertTrue(current['mapped'])
        self.assertEqual(current['generation'], surface['generation'])
        self.assertEqual(remote.act(key=core.ActorKeyRequest(keysym='F5', pressed=True)).error.code, core.UNIMPLEMENTED)

    def test_closed_view_cannot_attest_a_replacement_session(self):
        remote, identity, empty, app, surface, _ = self.local_context()
        self.attest(identity)
        self.assertTrue(remote.call(close=session.WorldSessionClose()).HasField('closed'))
        replacement = self.world_process.connect(self)
        opened = self.authenticate_second(replacement)
        self.assertTrue(opened.HasField('opened'), opened)
        self.assertNotEqual(opened.opened.geometry_view_id, identity.view_id)
        denied = self.retained_view(identity)
        self.assertEqual(denied.error.code, core.FORBIDDEN, denied)
        self.assertEqual(self.pixels(replacement).image.rgb8, empty.image.rgb8)
        fresh = local_view.ActorLocalViewIdentity()
        fresh.CopyFrom(identity)
        fresh.view_id = opened.opened.geometry_view_id
        self.attest(fresh)
        self.assertIn(surface['id'], {item.id for item in self.pixels(replacement).visible})


if __name__ == '__main__':
    names = [name for name in LocalActorViewTests.__dict__ if name.startswith('test_')]
    suite = unittest.TestSuite(LocalActorViewTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
