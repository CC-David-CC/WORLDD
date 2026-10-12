"""Independent native requests against shared device administration."""
import unittest
import test_resource_isolation as resources
import security_pb2 as pb


class DeviceAdministrationTests(unittest.TestCase):
    setUp = resources.ResourceIsolationTests.setUp
    start = resources.ResourceIsolationTests.start
    connect = resources.ResourceIsolationTests.connect
    scene = resources.ResourceIsolationTests.scene
    denied = resources.ResourceIsolationTests.denied

    def keyboard_mapping(self, client):
        return client.operation(101, client.pack('BBH', client.setup[26], 1, 0), reply=True)

    def test_application_cannot_replace_keyboard_mapping(self):
        self.scene()
        self.denied(self.other, 100, self.other.pack('BBH', self.other.setup[26], 1, 0)
                    +self.other.pack('I', 0x61), minor=1)

    def test_application_cannot_change_keyboard_controls(self):
        supervisor = self.scene()
        before = supervisor.operation(103, reply=True)
        self.denied(self.other, 102, self.other.pack('II', 2, 0))
        after = supervisor.operation(103, reply=True)
        self.assertEqual(after[0][8:], before[0][8:])
        self.assertEqual(after[1], before[1])

    def test_application_cannot_ring_shared_keyboard_bell(self):
        self.scene()
        self.denied(self.other, 104)

    def test_application_cannot_change_pointer_controls(self):
        supervisor = self.scene()
        before = supervisor.operation(106, reply=True)[0][8:]
        self.denied(self.other, 105, self.other.pack('hhhBB', 2, 1, 4, 1, 1))
        self.assertEqual(supervisor.operation(106, reply=True)[0][8:], before)

    def test_application_cannot_replace_pointer_mapping(self):
        self.scene()
        header, body = self.other.operation(117, reply=True)
        count = header[1]
        self.assertGreater(count, 1)
        mapping = bytearray(body[:count])
        mapping[0], mapping[1] = mapping[1], mapping[0]
        self.denied(self.other, 116, bytes(mapping), minor=count, reply=True)

    def test_application_cannot_clear_modifier_mapping(self):
        self.scene()
        self.denied(self.other, 118, minor=0, reply=True)

    def test_workspace_membership_does_not_grant_device_administration(self):
        self.scene(same_workspace=True)
        self.denied(self.other, 102, self.other.pack('II', 2, 0))

    def test_broker_has_no_device_administration_authority(self):
        supervisor = self.scene()
        broker = self.connect(supervisor.issue(pb.BROKER, b'', b'').cookie)
        self.denied(broker, 105, broker.pack('hhhBB', 2, 1, 4, 1, 1))

    def test_swapped_requests_cannot_bypass_device_administration(self):
        self.scene(endian='>')
        self.denied(self.other, 102, self.other.pack('II', 2, 0))
        self.denied(self.other, 105, self.other.pack('hhhBB', 2, 1, 4, 1, 1))
        self.denied(self.other, 100, self.other.pack('BBH', self.other.setup[26], 1, 0)
                    +self.other.pack('I', 0x61), minor=1)

    def test_application_can_read_public_keyboard_mapping(self):
        self.scene()
        header, body = self.keyboard_mapping(self.other)
        self.assertGreater(header[1], 0)
        self.assertEqual(len(body), header[1]*4)

    def test_application_can_read_public_pointer_mapping(self):
        self.scene()
        header, body = self.other.operation(117, reply=True)
        self.assertGreater(header[1], 0)
        self.assertGreaterEqual(len(body), header[1])
        self.assertTrue(any(body[:header[1]]))

    def test_application_can_read_public_modifier_mapping(self):
        self.scene()
        header, body = self.other.operation(119, reply=True)
        self.assertEqual(len(body), header[1]*8)
        self.assertGreater(header[1], 0)

    def test_trusted_services_can_configure_shared_devices(self):
        supervisor = self.scene()
        compositor = self.connect(supervisor.issue(pb.COMPOSITOR, b'', b'').cookie)
        for client in (supervisor, compositor):
            with self.subTest(role=client.inspect().role):
                self.assertIsNone(client.operation(102, client.pack('II', 2, 0)))
                self.assertIsNone(client.operation(105, client.pack('hhhBB', 2, 1, 4, 1, 1)))
                self.assertIsNone(client.operation(104))
                self.assertIsInstance(client.operation(103, reply=True), tuple)
                self.assertIsInstance(client.operation(106, reply=True), tuple)


if __name__ == '__main__':
    unittest.main(verbosity=2)
