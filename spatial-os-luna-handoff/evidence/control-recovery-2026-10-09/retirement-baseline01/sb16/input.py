"""Expose missing retirement on unchanged production through a real TLS connection."""
import unittest
import issuer_pb2 as pb
import test_authority_fencing as fencing


def varint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128); value >>= 7
    data.append(value)
    return bytes(data)


def field(number, value):
    return varint((number << 3) | 2) + varint(len(value)) + value


class RetirementBaseline(fencing.AuthorityFencingTests):
    def test_exact_fenced_generation_requires_retirement_endpoint(self):
        self.revoke_actor_for_test(); fence = self.fenced()
        self.assertEqual(self.barrier_for_test().pending_count, 1)
        before = self.snapshot_all()
        # The proposed additive request uses owner=1, fence_id=2, and digest=3.
        begin = field(1, self.fence_request().SerializeToString()) + field(2, fence.fence_id) + field(3, b'd' * 32)
        request = pb.IssuerRequest.FromString(b'\x08\x01' + field(32, begin))
        reply = self.peer.request(request)
        self.assertEqual(self.snapshot_all(), before)
        self.assertEqual(self.barrier_for_test().pending_count, 1)
        self.assertTrue(reply.HasField('challenge'), 'Retirement challenge not implemented: ' + str(reply))


if __name__ == '__main__':
    suite = unittest.TestSuite([RetirementBaseline('test_exact_fenced_generation_requires_retirement_endpoint')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
