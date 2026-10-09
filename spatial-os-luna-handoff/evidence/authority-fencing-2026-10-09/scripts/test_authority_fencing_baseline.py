"""Missing fencing must be observed through the actual issuer protobuf endpoint."""
import sys
from pathlib import Path
import unittest

SOURCE = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(SOURCE / 'tests/core'))
import authority_fence_baseline_pb2 as future
import test_launch_authorization as launch


class AuthorityFencingBaseline(launch.LaunchAuthorizationTests):
    def test_exact_active_supervised_launch_can_request_fencing(self):
        authorization = self.authorize()
        result = self.activate()
        self.assertTrue(result.HasField('authority_activation'), result)
        active = result.authority_activation
        self.assertTrue(active.active)
        request = future.IssuerRequest(protocol_version=1,
            authority_fence_begin=future.AuthorityFenceBegin(
                participant_id=active.registration.participant.id,
                registered_revision=active.registration.registered_revision,
                generation=active.generation, service_incarnation=active.service_incarnation,
                launch_id=authorization.request.launch_id,
                supervision_revision=authorization.request.supervision_revision,
                authorization_id=authorization.authorization_id))
        peer = self.connect()
        try:
            before = self.snapshot_all()
            reply = peer.request(request)
            self.assertEqual(self.snapshot_all(), before, 'A challenge request must not mutate durable state')
            self.assertTrue(reply.HasField('challenge'),
                'Missing authority fencing challenge from the production issuer: ' + str(reply))
        finally:
            peer.close()


if __name__ == '__main__':
    suite = unittest.TestSuite([AuthorityFencingBaseline('test_exact_active_supervised_launch_can_request_fencing')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
