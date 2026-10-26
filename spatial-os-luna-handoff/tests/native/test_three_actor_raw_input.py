"""Supplemental actor key test using independent raw and application witnesses."""
import unittest
from test_three_actor_applications import ThreeActorApplicationTests


class ThreeActorRawInputTests(ThreeActorApplicationTests):
    def launch_window(self,actor,index):
        return super().launch_window(actor,index,core_events=True)

    def native_observer(self,rows):
        return super().native_observer(rows,raw_keys=True)

    def test_raw_device_and_application_witnesses_preserve_high_rate_taps(self):
        # Keep every edge, ordering, device, application, and rate assertion.
        self.test_agent_key_tap_batches_preserve_every_edge_at_high_rate()


if __name__=='__main__':
    name='test_raw_device_and_application_witnesses_preserve_high_rate_taps'
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([ThreeActorRawInputTests(name)]))
    raise SystemExit(not result.wasSuccessful())
