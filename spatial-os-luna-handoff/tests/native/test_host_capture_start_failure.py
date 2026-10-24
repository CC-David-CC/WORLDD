"""Loss of a trusted capture adapter must not stop retained world services."""
import os
import unittest
import host_actor_controller_pb2 as actor
import test_host_continuous_capture as continuous


class CaptureStartFailureTests(continuous.ContinuousCaptureTests):
    def prepare(self, **options):
        config, descriptors = super().prepare(**options)
        # One endpoint supports initial capture. The next epoch must fail safely.
        for descriptor in list(config.trusted_capture_fds)[1:]:
            descriptors.remove(descriptor)
            os.close(descriptor)
        del config.trusted_capture_fds[1:]
        return config, descriptors

    def test_capture_start_failure_keeps_adopted_actor_and_world_services(self):
        self.ready_host(second=True)
        producer = self.producer()
        worlds = self.retained_pids()
        compositor = self.live_compositor_pids()
        self.assertEqual(len(compositor), 1)
        pids = {pid for group in worlds.values() for pid in group} | {self.server.pid, compositor[0]}
        starts = {pid: self.process_start(pid) for pid in pids}
        destination = self.worlds[1][0].world_id
        try:
            reply = self.shared('transition_proposal', 0, actor.HostActorTransitionRequest(
                protocol_version=1, transition_id=os.urandom(32), destination_world_id=destination))
        except AssertionError as error:
            self.fail('Capture startup terminated the host control channel: ' + str(error) + '\n' + self.host_log.read_text())
        self.assertTrue(reply.accepted, reply)
        self.assertTrue(reply.transition.actor_transition.destination_activation_confirmed, reply)
        producer.close()
        status = self.status(1)
        self.assertTrue(status.capture_retired, status)
        self.assertFalse(status.capture_active, status)
        self.assertIsNone(self.host_process.poll())
        self.assertEqual(self.retained_pids(), worlds)
        self.assertEqual({pid: self.process_start(pid) for pid in starts}, starts)
        self.assertEqual(list(self.state(1).axes), [0] * 6)
        self.stop(1)
        self.shutdown_checked()


if __name__ == '__main__':
    names = [name for name in CaptureStartFailureTests.__dict__ if name.startswith('test_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(CaptureStartFailureTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
