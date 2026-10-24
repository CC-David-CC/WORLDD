"""Production BrokerClient drives actual owner-bound broker observation watches."""
import socket
import subprocess
import unittest
import test_application_access as access
from test_observation_watch import ObservationWatchTests


class LiveObservationWatchTests(ObservationWatchTests):
    def register(self, owner=None):
        owner, upstream = super().register(owner)
        proxy, child = socket.socketpair()
        proxy.settimeout(5)
        path = self.root / ('observation-client-' + owner.launch.launch_id.hex() + '.log')
        log = path.open('wb')
        process = subprocess.Popen([str(access.workspace.WORLD.parent / 'spatial-broker-control-client-probe'),
            str(upstream.fileno()), str(self.broker.proc.pid)], stdin=child, stdout=child,
            stderr=log, pass_fds=(upstream.fileno(),))
        upstream.close()
        child.close()

        def finish():
            if proxy.fileno() >= 0:
                proxy.shutdown(socket.SHUT_WR)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
                raise
            finally:
                proxy.close()
                log.close()
            diagnostic = path.read_text()
            self.assertEqual(process.returncode, 0, diagnostic)
            for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
                self.assertNotIn(marker, diagnostic)
        self.addCleanup(finish)
        return owner, proxy


if __name__ == '__main__':
    names = [
        'test_watch_is_idempotent_and_inspectable_without_control_reservation',
        'test_watch_grants_no_workspace_observation_or_control_authority',
        'test_two_observers_and_an_independent_controller_coexist',
        'test_policy_change_notifies_only_the_observation_owner',
        'test_old_watch_blocks_barrier_until_exact_release',
        'test_stale_epoch_cannot_acquire_and_leaves_owner_usable',
        'test_invalidated_watch_requires_release_before_replacement',
        'test_another_owner_cannot_release_the_same_actor_view',
        'test_revoked_actor_can_release_observation_without_restored_rights',
        'test_invalidation_is_emitted_once',
    ]
    suite = unittest.TestSuite(LiveObservationWatchTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
