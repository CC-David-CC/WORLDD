"""Independent X11 attacks against shared server administration."""
import struct
import time
import unittest
import test_resource_isolation as resources
import security_pb2 as pb


class GlobalIsolationTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    connect = resources.ResourceIsolationTests.connect
    scene = resources.ResourceIsolationTests.scene
    denied = resources.ResourceIsolationTests.denied

    def test_application_cannot_grab_server(self):
        self.scene()
        self.denied(self.other, 36)
        self.assertEqual(self.owner.inspect().role, pb.APPLICATION)

    def test_application_cannot_ungrab_server(self):
        self.scene()
        self.denied(self.other, 37)

    def test_application_cannot_list_hosts(self):
        self.scene()
        self.denied(self.other, 110, reply=True)

    def test_application_cannot_change_host_acl(self):
        self.scene()
        self.denied(self.other, 109, self.other.pack('BBH', 0, 0, 4)+bytes([127, 0, 0, 1]))

    def test_application_cannot_change_access_control(self):
        self.scene()
        self.denied(self.other, 111, minor=1)

    def test_application_cannot_change_font_path(self):
        self.scene()
        self.denied(self.other, 51, self.other.pack('HH', 0, 0))

    def test_application_cannot_read_font_path(self):
        self.scene()
        self.denied(self.other, 52, reply=True)

    def test_application_can_list_public_fonts(self):
        self.scene()
        header, body = self.other.operation(49, self.other.pack('HH', 16, 1)+b'*', reply=True)
        count = struct.unpack_from(self.other.endian+'H', header, 8)[0]
        self.assertGreater(count, 0, 'Expected real public font definitions')
        self.assertTrue(body)

    def test_application_can_list_public_font_information(self):
        self.scene()
        client = self.other
        client.send(50, client.pack('HH', 1, 1)+b'*')
        sequence = client.sequence % 65536
        names = []
        while True:
            header = client.exact(32)
            self.assertEqual(header[0], 1)
            self.assertEqual(struct.unpack_from(client.endian+'H', header, 2)[0], sequence)
            size = struct.unpack_from(client.endian+'I', header, 4)[0]*4
            self.assertLessEqual(size, 1048576)
            body = client.exact(size)
            if header[1] == 0:
                break
            self.assertLess(len(names), 1)
            self.assertGreaterEqual(len(body), 28)
            properties = struct.unpack_from(client.endian+'H', body, 14)[0]
            offset = 28+properties*8
            name = body[offset:offset+header[1]]
            self.assertEqual(len(name), header[1])
            names.append(name)
        self.assertEqual(len(names), 1, 'Expected a real font information record')
        self.assertEqual(client.inspect().role, pb.APPLICATION)

    def test_application_cannot_reap_retained_service_resources(self):
        supervisor = self.scene()
        service = self.connect(supervisor.issue(pb.COMPOSITOR, b'', b'').cookie)
        window = service.create_window()
        self.assertIsNone(service.operation(112, minor=2))
        service.close()
        self.assertIn(window, supervisor.tree(supervisor.root))
        self.denied(self.other, 113, self.other.pack('I', 0))
        self.assertIn(window, supervisor.tree(supervisor.root))

    def test_application_cannot_read_screen_saver_settings(self):
        self.scene()
        self.denied(self.other, 108, reply=True)

    def test_application_cannot_change_screen_saver_settings(self):
        supervisor = self.scene()
        before = supervisor.operation(108, reply=True)[0][8:14]
        self.denied(self.other, 107, self.other.pack('hhBBH', 0, 0, 0, 0, 0))
        self.assertEqual(supervisor.operation(108, reply=True)[0][8:14], before)

    def test_application_cannot_force_screen_saver(self):
        self.scene()
        self.denied(self.other, 115, minor=0)

    def test_workspace_membership_does_not_grant_server_management(self):
        self.scene(same_workspace=True)
        self.denied(self.other, 111, minor=1)

    def test_supervisor_global_controls_remain_available(self):
        supervisor = self.scene()
        self.assertIsNone(supervisor.operation(36))
        self.assertIsNone(supervisor.operation(37))
        self.assertIsInstance(supervisor.operation(110, reply=True), tuple)
        self.assertIsInstance(supervisor.operation(52, reply=True), tuple)
        self.assertIsNone(supervisor.operation(51, supervisor.pack('HH', 0, 0)))
        self.assertIsNone(supervisor.operation(107, supervisor.pack('hhBBH', 0, 0, 0, 0, 0)))
        self.assertIsInstance(supervisor.operation(108, reply=True), tuple)
        self.assertIsNone(supervisor.operation(115, minor=0))
        self.assertEqual(self.other.inspect().role, pb.APPLICATION)

    def test_supervisor_can_reap_retained_service_resources(self):
        supervisor = self.scene()
        service = self.connect(supervisor.issue(pb.COMPOSITOR, b'', b'').cookie)
        window = service.create_window()
        self.assertIsNone(service.operation(112, minor=2))
        service.close()
        self.assertIn(window, supervisor.tree(supervisor.root))
        deadline = time.monotonic()+3
        while window in supervisor.tree(supervisor.root) and time.monotonic() < deadline:
            self.assertIsNone(supervisor.operation(113, supervisor.pack('I', 0)))
            time.sleep(.01)
        self.assertNotIn(window, supervisor.tree(supervisor.root))

    def test_compositor_can_query_global_settings(self):
        supervisor = self.scene()
        compositor = self.connect(supervisor.issue(pb.COMPOSITOR, b'', b'').cookie)
        for opcode in (52, 108, 110):
            with self.subTest(opcode=opcode):
                self.assertIsInstance(compositor.operation(opcode, reply=True), tuple)

    def test_broker_has_no_server_administration_authority(self):
        supervisor = self.scene()
        broker = self.connect(supervisor.issue(pb.BROKER, b'', b'').cookie)
        self.denied(broker, 36)
        self.denied(broker, 52, reply=True)
        self.denied(broker, 108, reply=True)

    def test_swapped_requests_cannot_bypass_global_checks(self):
        self.scene(endian='>')
        self.denied(self.other, 51, self.other.pack('HH', 0, 0))
        self.denied(self.other, 107, self.other.pack('hhBBH', 0, 0, 0, 0, 0))
        self.denied(self.other, 113, self.other.pack('I', 0))
        self.denied(self.other, 110, reply=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
