"""Independent X11 observations of protobuf-controlled native actor bindings."""
import struct
import time
import unittest
import input_pb2 as inp
import security_pb2 as sec
import test_resource_isolation as resources


class Client(resources.Client):
    def operation(self, opcode, payload=b'', minor=0, reply=False):
        self.send(opcode, payload, minor)
        target = self.sequence % 65536
        self.operation_sequence = getattr(self, 'operation_sequence', 0)+1
        data = sec.Request(protocol_version=1, request_id=self.operation_sequence,
                           inspect=sec.Inspect()).SerializeToString()
        self.send(self.opcode, self.pack('I', len(data))+data)
        fence = self.sequence % 65536
        result = None
        while True:
            head = self.exact(32)
            sequence = struct.unpack_from(self.endian+'H', head, 2)[0]
            if head[0] == 1:
                size = struct.unpack_from(self.endian+'I', head, 4)[0]*4
                if size > 1048576:
                    raise AssertionError('Excessive reply')
                body = self.exact(size)
                if sequence == fence:
                    size = struct.unpack_from(self.endian+'I', head, 8)[0]
                    if sec.Reply.FromString(body[:size]).code != sec.OK:
                        raise AssertionError('Fence failed')
                    if reply and result is None:
                        raise AssertionError('Missing reply')
                    return result
                if sequence != target or result is not None:
                    raise AssertionError('Unexpected reply sequence')
                result = (head, body)
            elif head[0] == 0:
                if sequence != target or result is not None:
                    raise AssertionError('Unexpected error')
                result = head[1]
            elif 2 <= (head[0] & 127) <= 34:
                if not hasattr(self, 'events'):
                    self.events = []
                self.events.append(head)
            else:
                raise AssertionError('Unexpected native event')


class ActorBindingTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start

    def connect(self, cookie, **options):
        client = Client(self.display, cookie, **options)
        client.events = []
        self.clients.append(client)
        return client

    def setup_scene(self, endian='<'):
        self.supervisor = self.start()
        self.owner_grant = self.supervisor.issue(workspace=b'one', application=b'editor')
        self.owner = self.connect(self.owner_grant.cookie, endian=endian)
        self.peer = self.connect(self.supervisor.issue(workspace=b'one', application=b'editor').cookie)
        self.other = self.connect(self.supervisor.issue(workspace=b'two', application=b'editor').cookie)
        self.window = self.owner.create_window()
        self.other_window = self.other.create_window()
        for client, window in ((self.owner, self.window), (self.other, self.other_window)):
            self.assertIsNone(client.operation(2, client.pack('III', window, 1 << 11, 3 | 4 | 8 | 64)))
            self.assertIsNone(client.operation(8, client.pack('I', window)))
        self.compositor = self.connect(self.supervisor.issue(sec.COMPOSITOR, b'', b'').cookie)
        self.assertIsNone(self.compositor.operation(2, self.compositor.pack('III', self.compositor.root, 1 << 11, 1 << 20)))
        name = b'SPATIAL-INPUT'
        head, _ = self.compositor.operation(98, self.compositor.pack('HH', len(name), 0)+name, reply=True)
        self.assertTrue(head[8])
        self.input_opcode = head[9]
        self.input_sequence = 0
        self.seat = self.call(open=inp.Empty())
        self.second = self.call(open=inp.Empty())

    def call(self, expected=inp.OK, **op):
        self.input_sequence += 1
        data = inp.Request(protocol_version=1, request_id=self.input_sequence, **op).SerializeToString()
        head, body = self.compositor.operation(self.input_opcode, self.compositor.pack('I', len(data))+data, reply=True)
        size = struct.unpack_from('<I', head, 8)[0]
        reply = inp.Reply.FromString(body[:size])
        self.assertEqual(reply.request_id, self.input_sequence)
        self.assertEqual(reply.code, expected, reply)
        return reply

    def target(self, window):
        return self.call(inspect=inp.Inspect(xid=window)).target

    def click(self, seat=None, window=None, expected=inp.OK):
        seat = seat or self.seat
        window = window or self.window
        target = self.target(window)
        self.call(expected, button=inp.Button(seat=seat.seat, target=target, x=4, y=5,
                                             button=1, pressed=True, focus=True))
        if expected == inp.OK:
            self.call(button=inp.Button(seat=seat.seat, target=target, x=4, y=5, button=1))

    def focus(self, client):
        head, _ = client.operation(43, reply=True)
        return struct.unpack_from(client.endian+'I', head, 8)[0]

    def key(self, code, pressed, seat=None):
        self.call(key=inp.Key(seat=(seat or self.seat).seat, keycode=code, pressed=pressed))

    def held(self, client, code):
        head, body = client.operation(44, reply=True)
        bitmap = head[8:]+body
        self.assertEqual(len(bitmap), 32)
        return bool(bitmap[code//8] & (1 << (code % 8)))

    def device(self, window):
        root = self.supervisor
        name = b'XInputExtension'
        head, _ = root.operation(98, root.pack('HH', len(name), 0)+name, reply=True)
        self.assertTrue(head[8])
        head, _ = root.operation(head[9], root.pack('I', window), minor=45, reply=True)
        self.assertTrue(head[8])
        return struct.unpack_from('<H', head, 10)[0]

    def cancel(self, seat=None):
        self.call(cancel=inp.Seat(id=(seat or self.seat).seat))

    def test_application_uses_genuine_actor_device(self):
        self.setup_scene()
        self.click()
        self.assertEqual(self.device(self.window), self.seat.pointer_device)
        self.assertEqual(self.focus(self.owner), self.window)

    def test_group_connections_share_actor_focus_and_keys(self):
        self.setup_scene()
        self.click()
        self.assertEqual(self.focus(self.peer), self.window)
        self.key(38, True)
        self.assertTrue(self.held(self.owner, 38))
        self.assertTrue(self.held(self.peer, 38))
        self.assertFalse(self.held(self.other, 38))
        self.key(38, False)

    def test_swapped_application_observes_actor_state(self):
        self.setup_scene(endian='>')
        self.click()
        self.assertEqual(self.focus(self.owner), self.window)
        self.key(38, True)
        self.assertTrue(self.held(self.owner, 38))

    def test_second_actor_cannot_take_same_application(self):
        self.setup_scene()
        self.click()
        self.click(self.second, expected=inp.BUSY)
        self.assertEqual(self.device(self.window), self.seat.pointer_device)

    def test_different_applications_have_simultaneous_controllers(self):
        self.setup_scene()
        self.click()
        self.click(self.second, self.other_window)
        self.assertEqual(self.device(self.window), self.seat.pointer_device)
        self.assertEqual(self.device(self.other_window), self.second.pointer_device)
        self.key(38, True)
        self.key(39, True, self.second)
        self.assertTrue(self.held(self.owner, 38))
        self.assertFalse(self.held(self.owner, 39))
        self.assertTrue(self.held(self.other, 39))
        self.assertFalse(self.held(self.other, 38))

    def test_cancel_releases_real_key_before_returning_to_idle(self):
        self.setup_scene()
        idle = self.device(self.window)
        self.click()
        self.key(38, True)
        self.assertTrue(self.held(self.owner, 38))
        self.owner.events.clear()
        self.cancel()
        self.assertEqual(self.device(self.window), idle)
        self.assertFalse(self.held(self.owner, 38))
        self.assertEqual(self.focus(self.owner), 0)
        self.assertTrue(any(e[0] == 3 and e[1] == 38 for e in self.owner.events))

    def test_cancel_preserves_other_actor(self):
        self.setup_scene()
        self.click()
        self.click(self.second, self.other_window)
        self.key(39, True, self.second)
        self.cancel()
        self.assertEqual(self.focus(self.other), self.other_window)
        self.assertTrue(self.held(self.other, 39))

    def test_transfer_to_new_application_releases_old_keys(self):
        self.setup_scene()
        idle = self.device(self.window)
        self.click()
        self.key(38, True)
        self.click(window=self.other_window)
        self.assertEqual(self.device(self.window), idle)
        self.assertEqual(self.focus(self.owner), 0)
        self.assertEqual(self.device(self.other_window), self.seat.pointer_device)
        self.assertFalse(self.held(self.other, 38))

    def test_transfer_to_second_actor_after_cancel(self):
        self.setup_scene()
        self.click()
        self.cancel()
        self.click(self.second)
        self.assertEqual(self.device(self.window), self.second.pointer_device)
        self.key(38, True, self.second)
        self.assertTrue(self.held(self.owner, 38))

    def test_late_connection_inherits_current_actor(self):
        self.setup_scene()
        self.click()
        late = self.connect(self.supervisor.issue(workspace=b'one', application=b'editor').cookie)
        self.assertEqual(self.focus(late), self.window)
        window = late.create_window()
        self.assertEqual(self.device(window), self.seat.pointer_device)

    def test_application_dialog_uses_same_lease(self):
        self.setup_scene()
        popup = self.peer.create_window()
        self.assertIsNone(self.compositor.operation(8, self.compositor.pack('I', popup)))
        self.click()
        self.click(window=popup)
        self.assertEqual(self.focus(self.owner), popup)
        self.click(self.second, popup, expected=inp.BUSY)

    def test_application_focus_change_stays_within_group(self):
        self.setup_scene()
        popup = self.peer.create_window()
        self.assertIsNone(self.compositor.operation(8, self.compositor.pack('I', popup)))
        self.click()
        self.assertIsNone(self.peer.operation(42, self.peer.pack('II', popup, 0)))
        self.assertEqual(self.focus(self.owner), popup)
        self.assertEqual(self.peer.operation(42, self.peer.pack('II', self.other_window, 0)), 10)

    def test_unmap_releases_binding(self):
        self.setup_scene()
        idle = self.device(self.window)
        self.click()
        self.key(38, True)
        self.assertTrue(self.held(self.owner, 38))
        self.assertIsNone(self.owner.operation(10, self.owner.pack('I', self.window)))
        self.assertEqual(self.device(self.window), idle)
        self.assertEqual(self.focus(self.owner), 0)
        self.assertFalse(self.held(self.owner, 38))

    def test_controller_disconnect_releases_binding(self):
        self.setup_scene()
        idle = self.device(self.window)
        self.click()
        self.key(38, True)
        self.assertEqual(self.device(self.window), self.seat.pointer_device)
        self.assertTrue(self.held(self.owner, 38))
        self.compositor.close()
        deadline = time.monotonic()+2
        while self.device(self.window) != idle and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertEqual(self.device(self.window), idle)
        self.assertEqual(self.focus(self.owner), 0)
        self.assertFalse(self.held(self.owner, 38))

    def test_revoked_target_connection_cancels_without_closing_peer(self):
        self.setup_scene()
        self.click()
        self.key(38, True)
        self.assertTrue(self.held(self.peer, 38))
        self.assertEqual(self.supervisor.call(revoke=sec.Revoke(credential_id=self.owner_grant.id)).code, sec.OK)
        self.assertEqual(self.focus(self.peer), 0)
        self.assertFalse(self.held(self.peer, 38))
        self.assertEqual(self.peer.inspect().application, b'editor')

    def test_service_window_cannot_receive_application_binding(self):
        self.setup_scene()
        window = self.supervisor.create_window()
        self.assertIsNone(self.compositor.operation(8, self.compositor.pack('I', window)))
        self.click(window=window, expected=inp.FORBIDDEN)

    def test_cancel_clears_locked_and_held_modifiers(self):
        self.setup_scene()
        self.click()
        self.key(66, True)
        self.key(66, False)
        self.key(50, True)
        head, _ = self.owner.operation(38, self.owner.pack('I', self.window), reply=True)
        self.assertEqual(struct.unpack_from('<H', head, 24)[0] & 3, 3)
        self.cancel()
        self.click(window=self.other_window)
        head, _ = self.other.operation(38, self.other.pack('I', self.other_window), reply=True)
        self.assertEqual(struct.unpack_from('<H', head, 24)[0] & 3, 0)

    def test_busy_acquisition_preserves_existing_application(self):
        self.setup_scene()
        self.click()
        self.click(self.second, self.other_window)
        self.key(39, True, self.second)
        self.click(self.second, expected=inp.BUSY)
        self.assertEqual(self.device(self.other_window), self.second.pointer_device)
        self.assertTrue(self.held(self.other, 39))

    def test_cancel_delivers_button_release(self):
        self.setup_scene()
        target = self.target(self.window)
        self.call(button=inp.Button(seat=self.seat.seat, target=target, x=4, y=5,
                                   button=1, pressed=True, focus=True))
        head, _ = self.owner.operation(38, self.owner.pack('I', self.window), reply=True)
        self.assertTrue(struct.unpack_from('<H', head, 24)[0] & 256)
        self.owner.events.clear()
        self.cancel()
        self.assertEqual(self.focus(self.owner), 0)
        self.assertTrue(any(e[0] == 5 and e[1] == 1 for e in self.owner.events))

    def test_new_application_does_not_inherit_motion_history(self):
        self.setup_scene()
        target = self.target(self.window)
        self.call(button=inp.Button(seat=self.seat.seat, target=target, x=4, y=5,
                                   button=1, pressed=True, focus=True))
        self.assertEqual(self.device(self.window), self.seat.pointer_device)
        self.call(motion=inp.Motion(seat=self.seat.seat, target=target, x=11, y=12))
        head, body = self.owner.operation(39, self.owner.pack('III', self.window, 1, 0), reply=True)
        self.assertGreater(struct.unpack_from('<I', head, 8)[0], 0)
        cutoff = max(t for t, x, y in struct.iter_unpack('<Ihh', body))
        time.sleep(.005)
        self.cancel()
        self.click(window=self.other_window)
        # Button events can add fresh samples. Query only the previous binding.
        head, _ = self.other.operation(39, self.other.pack('III', self.other_window, 1, cutoff), reply=True)
        self.assertEqual(struct.unpack_from('<I', head, 8)[0], 0)

    def grab(self, client, window, keyboard=True, synchronous=False):
        mode = 0 if synchronous else 1
        payload = client.pack('IIBB2x', window, 0, mode, mode) if keyboard else client.pack('IHBBIII', window, 12, mode, mode, 0, 0, 0)
        head, _ = client.operation(31 if keyboard else 26, payload, reply=True)
        return head[1]

    def test_binding_releases_idle_active_grabs(self):
        self.setup_scene()
        self.assertEqual(self.grab(self.owner, self.window), 0)
        self.assertEqual(self.grab(self.peer, self.window), 1)
        self.click()
        self.assertEqual(self.grab(self.peer, self.window), 0)
        self.assertEqual(self.device(self.window), self.seat.pointer_device)

    def test_cancel_releases_active_grabs(self):
        self.setup_scene()
        self.click()
        self.assertEqual(self.grab(self.owner, self.window), 0)
        self.assertEqual(self.grab(self.owner, self.window, keyboard=False), 0)
        self.assertEqual(self.grab(self.peer, self.window), 1)
        self.cancel()
        self.click(self.second)
        self.assertEqual(self.grab(self.peer, self.window), 0)
        self.assertEqual(self.grab(self.peer, self.window, keyboard=False), 0)

    def test_cancel_drains_frozen_key_events_before_transfer(self):
        self.setup_scene()
        self.click()
        self.assertEqual(self.device(self.window), self.seat.pointer_device)
        self.assertEqual(self.grab(self.owner, self.window, synchronous=True), 0)
        self.assertEqual(self.grab(self.peer, self.window), 1)
        self.key(38, True)
        self.cancel()
        self.click(window=self.other_window)
        self.assertFalse(self.held(self.other, 38))
        self.assertFalse(self.held(self.owner, 38))
        self.assertFalse(any(e[0] in (2, 3) and e[1] == 38 for e in self.other.events))

    def test_passive_key_grab_survives_actor_close(self):
        self.setup_scene()
        # GrabKey: window, AnyModifier, keycode, asynchronous pointer/keyboard.
        self.assertIsNone(self.owner.operation(33, self.owner.pack('IHBBB3x', self.window, 32768, 38, 1, 1)))
        self.click()
        self.key(38, True)
        self.assertEqual(self.grab(self.peer, self.window), 1)
        self.call(close=inp.Seat(id=self.seat.seat))
        self.click(self.second)
        self.key(38, True, self.second)
        self.assertTrue(self.held(self.owner, 38))
        self.assertEqual(self.grab(self.peer, self.window), 1)
        self.key(38, False, self.second)
        self.assertEqual(self.grab(self.peer, self.window), 0)

    def test_actor_close_returns_application_to_idle(self):
        self.setup_scene()
        idle = self.device(self.window)
        self.click()
        self.key(38, True)
        self.call(close=inp.Seat(id=self.seat.seat))
        self.assertEqual(self.device(self.window), idle)
        self.assertEqual(self.focus(self.owner), 0)
        self.assertFalse(self.held(self.owner, 38))
        self.click(self.second)
        self.assertEqual(self.device(self.window), self.second.pointer_device)


if __name__ == '__main__':
    unittest.main(verbosity=2)
