"""A new world's checked projection must belong to its new controls context."""
import os
import select
import unittest
import core_pb2 as core
import host_actor_controller_pb2 as actor
import test_host_shared_controls as shared


class ProjectionHandoffTests(shared.SharedHostControlsTests):
    def retain_services(self):
        worlds = self.retained_pids()
        compositors = self.live_compositor_pids()
        self.assertEqual(len(compositors), 1)
        pids = {pid for group in worlds.values() for pid in group}
        pids.update([self.server.pid, compositors[0]])
        return worlds, {pid: self.process_start(pid) for pid in pids}, self.compositor_pin()

    def assert_services_retained(self, retained):
        worlds, starts, pin = retained
        self.assertEqual(self.retained_pids(), worlds)
        self.assertEqual({pid: self.process_start(pid) for pid in starts}, starts)
        self.assertEqual(select.select([pin], [], [], 0)[0], [])

    def config_file(self, name, value):
        if isinstance(value, core.RuntimeConfig):
            copied = core.RuntimeConfig(); copied.CopyFrom(value)
            copied.render.vertical_fov = 1.1 if not getattr(self, 'world_instance_suffix', '') else 1.8
            value = copied
        return super().config_file(name, value)

    def go(self, generation, destination):
        reply = self.shared('transition_proposal', generation, actor.HostActorTransitionRequest(
            protocol_version=1, transition_id=os.urandom(32), destination_world_id=destination))
        self.assertTrue(reply.accepted, reply)
        self.assertTrue(reply.transition.actor_control_available, reply)
        self.assertTrue(reply.transition.actor_transition.destination_activation_confirmed, reply)
        return reply

    def test_different_fov_a_b_a_retains_native_controls_and_services(self):
        self.ready_host(second=True)
        retained = self.retain_services()
        epochs = [self.status().capture_generation]
        a, b = [world.world_id for world, _, _ in self.worlds]
        for generation, destination in ((0, b), (1, a)):
            self.go(generation, destination)
            status = self.status(generation + 1)
            self.assertEqual((status.viewport_width, status.viewport_height), (128, 128))
            self.assertGreater(status.capture_generation, epochs[-1])
            epochs.append(status.capture_generation)
            self.stop(generation + 1)
            self.assert_services_retained(retained)
            pixels = self.foreground_pixels()
            self.assertTrue(any(any(pixels[channel::4]) for channel in range(3)))
        self.shutdown_checked()

    def test_denied_observation_then_different_fov_return_keeps_control_authority(self):
        self.ready_host(destination_observe_denied=True)
        retained = self.retain_services()
        a, b = [world.world_id for world, _, _ in self.worlds]
        denied = self.go(0, b)
        self.assertTrue(denied.transition.actor_transition.host_presentation_selection_pending, denied)
        self.assertTrue(self.status(1).available)
        self.assert_foreground_blank()
        self.assert_services_retained(retained)
        self.stop(1)
        self.go(1, a)
        status = self.status(2)
        self.assertEqual((status.viewport_width, status.viewport_height), (128, 128))
        self.assert_services_retained(retained)
        self.stop(2)
        self.shutdown_checked()


if __name__ == '__main__':
    names = [name for name in ProjectionHandoffTests.__dict__ if name.startswith('test_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ProjectionHandoffTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
