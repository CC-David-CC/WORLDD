"""Real filtered pixels through authenticated actor sessions and native C clients."""
import os
import select
import socket
import struct
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import test_world_application_control as control
import test_world_authority as world_authority
import issuer_pb2 as issuer
import geometry_native_pb2 as native_witness
from harness.wire import frame
from harness.oracles import pose
from test_world_identity import receive
from geometry_process_identity import collect

core = control.core
grant = control.grant
session = control.session
observation = control.observation


class ActorPixelTests(observation.ApplicationObservationTests):
    probe = control.WorldApplicationControlTests.probe
    probe_events = control.WorldApplicationControlTests.probe_events
    probe_command = control.WorldApplicationControlTests.probe_command
    control_action = control.WorldApplicationControlTests.control_action
    actor_aim = control.WorldApplicationControlTests.actor_aim
    actor_pointer = control.WorldApplicationControlTests.actor_pointer
    send_key_event = control.WorldApplicationControlTests.send_key_event
    actor_state = control.WorldApplicationControlTests.actor_state

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

    @staticmethod
    def proc_start_ticks(pid):
        value = Path(f'/proc/{pid}/stat').read_text()
        fields = value[value.rfind(')') + 2:].split()
        return int(fields[19])

    def managed_probe_parent(self, probe_id):
        # Match the real probe's complete argv, then pin its direct app parent.
        matches = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                argv = (entry / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
                if argv != [b'/project/probe', b'--id', probe_id.encode()]:
                    continue
                status = (entry / 'status').read_text()
                cgroup = (entry / 'cgroup').read_text()
            except OSError:
                continue
            parent = next((int(line.split()[1]) for line in status.splitlines() if line.startswith('PPid:')), 0)
            if parent > 1:
                matches.append((int(entry.name), parent, cgroup))
        self.assertEqual(len(matches), 1, f'Expected one real native probe process for {probe_id}: {matches}')
        probe_pid, app_pid, probe_cgroup = matches[0]
        probe_status = Path(f'/proc/{probe_pid}/status').read_text()
        app_status = Path(f'/proc/{app_pid}/status').read_text()
        app_argv = Path(f'/proc/{app_pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        def namespace_pids(status):
            line = next(line for line in status.splitlines() if line.startswith('NSpid:'))
            return [int(value) for value in line.split()[1:]]
        probe_namespace_pids = namespace_pids(probe_status)
        app_namespace_pids = namespace_pids(app_status)
        self.assertEqual(probe_namespace_pids[0], probe_pid, 'Probe discovery must use the host /proc PID')
        self.assertEqual(app_namespace_pids[0], app_pid, 'Application discovery must use the host /proc PID')
        app_command = b'\0'.join(app_argv)
        self.assertIn(b'/project/probe', app_command, 'Probe parent must be the launched application command')
        self.assertIn(probe_id.encode(), app_command, 'Probe parent command must contain this probe identity')
        self.assertEqual(os.stat(f'/proc/{app_pid}/ns/pid').st_ino,
                         os.stat(f'/proc/{probe_pid}/ns/pid').st_ino,
                         'Managed app and probe must share their application PID namespace')
        self.assertEqual(Path(f'/proc/{app_pid}/cgroup').read_text(), probe_cgroup,
                         'Managed application and its native probe must share the production process scope')
        broker_pid = self.broker.proc.pid
        ancestor = app_pid
        broker_descendant = False
        for _ in range(16):
            if ancestor == broker_pid:
                broker_descendant = True
                break
            parent_status = Path(f'/proc/{ancestor}/status').read_text()
            ancestor = next((int(line.split()[1]) for line in parent_status.splitlines()
                             if line.startswith('PPid:')), 0)
            if ancestor <= 1:
                break
        self.assertTrue(broker_descendant,
                        f'Exact native probe parent {app_pid} must descend from known broker PID {broker_pid}')
        return app_pid

    def compositor_pid(self):
        children = Path(f'/proc/{self.world_process.proc.pid}/task/{self.world_process.proc.pid}/children').read_text().split()
        expected = (control.access.workspace.WORLD.parent / 'spatialwm').resolve(strict=True)
        matches = []
        for value in children:
            pid = int(value)
            try:
                executable = Path(f'/proc/{pid}/exe').resolve(strict=True)
            except OSError:
                continue
            if executable == expected:
                matches.append(pid)
        self.assertEqual(len(matches), 1, f'Expected one spatialwm child of worldd at {expected}: {matches}')
        return matches[0]

    def process_identity(self, pid):
        ticks = self.proc_start_ticks(pid)
        return collect([(pid, ticks)], protocol=native_witness)[0]

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

    def test_observation_and_control_revocation_preserve_application_for_independent_controller(self):
        self.setup_world(include_third_actor=True)
        empty = self.pixels()
        app, surface = self.probe()
        self.addCleanup(self._release_probe, app)
        probe_id = f'controlled-{self.probe_number}'
        self.grant_viewer([grant.WORKSPACE_ACCESS, *control.access.workspace.PROJECT])
        b_observe = self.grant_viewer([grant.APPLICATION_OBSERVE], app)
        b_control = self.grant_viewer([grant.APPLICATION_CONTROL], app)
        self.grant_viewer([grant.WORKSPACE_ACCESS, *control.access.workspace.PROJECT], claims=self.third)
        self.grant_viewer([grant.APPLICATION_OBSERVE], app, claims=self.third)
        self.grant_viewer([grant.APPLICATION_CONTROL], app, claims=self.third)

        # A's own roots remain untouched. B and C have separate grants from A.
        self.assertNotEqual(self.pixels().image.rgb8, empty.image.rgb8)
        self.probe_command(app, 'sync')
        baseline = self.probe_events(app)
        before_event_count = len(baseline)
        self.actor_aim(self.viewer)
        self.actor_pointer(True, self.viewer)
        self.send_key_event('Shift_L', True, self.viewer)
        self.send_key_event('a', True, self.viewer)
        held = self.until(lambda: self.probe_events(app), lambda rows:
            any(r.get('event') == 'button' and r.get('pressed') and r.get('button') == 1 for r in rows[before_event_count:]) and
            any(r.get('event') == 'key' and r.get('pressed') and r.get('keysym') == 0xffe1 for r in rows[before_event_count:]) and
            any(r.get('event') == 'key' and r.get('pressed') and r.get('keysym') in (ord('a'), ord('A')) for r in rows[before_event_count:]))
        b_keycodes = {row['keycode'] for row in held[before_event_count:]
                      if row.get('event') == 'key' and row.get('pressed') and
                      row.get('keysym') in (0xffe1, ord('a'), ord('A'))}
        self.assertGreaterEqual(len(b_keycodes), 2, 'B must hold a modifier and a nonmodifier key')
        self.assertGreater(len(held), before_event_count)

        self.actor_aim(self.third_peer)
        self.denied(self.third_peer.act(pointer=core.ActorPointerRequest(button=1, pressed=True)), core.BUSY)

        app_pid = self.managed_probe_parent(probe_id)
        compositor_pid = self.compositor_pid()
        pinned = {
            'application': self.process_identity(app_pid),
            'worldd': self.process_identity(self.world_process.proc.pid),
            'spatialwm': self.process_identity(compositor_pid),
            'xserver': self.process_identity(self.native.server.pid),
        }
        self.revoke_grant(self.peer, b_control)
        self.revoke_grant(self.peer, b_observe)

        self.addCleanup(self._release_third_controller)
        self.actor_aim(self.third_peer)
        self.actor_pointer(True, self.third_peer)
        self.send_key_event('c', True, self.third_peer)
        rows = self.until(lambda: self.probe_events(app), lambda values:
            any(r.get('event') == 'key' and r.get('pressed') and r.get('text_hex') == '63' for r in values[before_event_count:]))
        fresh = rows[before_event_count:]
        # Keep the pre-B baseline, but exclude events before B's holds were witnessed.
        transition = fresh[len(held) - before_event_count:]
        release_button = next((i for i, r in enumerate(transition)
                               if r.get('event') == 'button' and not r.get('pressed')
                               and r.get('button') == 1), None)
        released_keys = {r.get('keycode') for r in transition if r.get('event') == 'key' and not r.get('pressed')}
        c_button = next((i for i, r in enumerate(transition)
                         if r.get('event') == 'button' and r.get('pressed')
                         and r.get('button') == 1), None)
        self.assertIsNotNone(release_button, 'B held button 1 must produce a real native release')
        self.assertIsNotNone(c_button, 'C must produce a fresh native button 1 press')
        self.assertTrue(b_keycodes.issubset(released_keys), 'B held keys must each produce a real native release')
        self.assertLess(release_button, c_button, 'B button 1 release must precede C native button 1 press')
        self.assertTrue(all(i < c_button for i, row in enumerate(transition)
                            if row.get('event') == 'key' and not row.get('pressed') and row.get('keycode') in b_keycodes),
                        'B key releases must precede C native button 1 press')
        self.assertTrue(any(r.get('event') == 'key' and r.get('pressed') and r.get('text_hex') == '63'
                            and r.get('xid') == next(v['xid'] for v in self.probe_events(app) if v.get('event') == 'ready')
                            for r in fresh), 'C must deliver input to the same live native window')

        hidden = self.pixels(self.viewer)
        self.assertEqual(hidden.image.rgb8, empty.image.rgb8)
        self.assertFalse(hidden.visible, 'Revoked observation must hide protected surface metadata')
        self.assertNotEqual(self.pixels(self.peer).image.rgb8, empty.image.rgb8,
                            'A retains valid application observation and execution authority')
        denied = self.viewer.act(key=core.ActorKeyRequest(keysym='x', pressed=True))
        self.denied(denied, core.FORBIDDEN)
        self.assertFalse(denied.HasField('action'), 'Revoked control must not produce a successful actor action')

        after = {
            'application': self.process_identity(app_pid),
            'worldd': self.process_identity(self.world_process.proc.pid),
            'spatialwm': self.process_identity(compositor_pid),
            'xserver': self.process_identity(self.native.server.pid),
        }
        self.assertEqual(after, pinned, 'Application, worldd, and X server identities must survive revocation')
        self.assertEqual(self.world_process.management('surface.get', id=surface['id'])['mapped'], True)

    def _release_third_controller(self):
        failures = []
        state = None
        try:
            inspection = self.third_peer.call(inspect=session.WorldSessionInspect())
            self.assertTrue(inspection.HasField('state'), inspection)
            state = inspection.state
        except (AssertionError, OSError, EOFError) as error:
            failures.append(f'state inspection: {error}')
        if state is not None:
            for key in list(state.held_keys):
                try:
                    self.control_action(self.third_peer, key=core.ActorKeyRequest(keysym=key, pressed=False))
                except (AssertionError, OSError, EOFError) as error:
                    failures.append(f'key {key!r}: {error}')
            for button in list(state.held_buttons):
                try:
                    self.control_action(self.third_peer, pointer=core.ActorPointerRequest(button=button, pressed=False))
                except (AssertionError, OSError, EOFError) as error:
                    failures.append(f'button {button}: {error}')
        try:
            self.control_action(self.third_peer, stop=core.ActorStopRequest())
        except (AssertionError, OSError, EOFError) as error:
            failures.append(f'stop: {error}')
        if failures:
            self.fail('C input cleanup failed: ' + '; '.join(failures))

    def _release_probe(self, app):
        (self.data(app) / 'release').touch()
        result = self.world_process.management('process.wait', id=app.application.decode(), timeout_seconds=5)
        self.assertEqual(result['exit_code'], 0, f'Probe application exited unsuccessfully: {result}')

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
            # Scene and post-render checks precede the queued reply's first-write check.
            if count == 3:
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

    def setup_world(self, rights=None, legacy=False, include_third_actor=False):
        rights = [*control.access.ALL, grant.WORKSPACE_PRESENTATION_ATTACH] if rights is None else rights
        return super().setup_world(rights=rights, legacy=legacy, include_third_actor=include_third_actor)

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
            transform=core.Pose(position=core.Vec3(x=.8, z=-2),
                                orientation=core.Quaternion(w=1)),
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
        self.assertEqual(raced.error.code, core.UNAUTHORIZED, raced)
        self.assertEqual(raced.error.message,
            'World session changed during operation', raced)
        self.assertFalse(raced.HasField('action'), 'revoked observation must return no pixel payload')
        self.assertTrue(self.presentation_relay.delivered.is_set())
        self.assertFalse(self.presentation_relay.errors)

def suite():
    tests = [ActorPixelTests(name) for name in vars(ActorPixelTests) if name.startswith('test_')]
    tests.append(PresentationPixelFenceTests('test_pixels_are_withheld_when_later_presentation_check_revokes_actor'))
    return unittest.TestSuite(tests)


if __name__ == '__main__':
    unittest.main(defaultTest='suite', verbosity=2)
