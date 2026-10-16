#!/usr/bin/env python3
"""Pure fault-control timing checks; no services, real sleeps, or global clock changes."""
import importlib.util
from pathlib import Path
import unittest

RELAY=Path(__file__).resolve().parents[1]/'native/storage_owner_copy_fault_relay.py'
SPEC=importlib.util.spec_from_file_location('copy_fault_relay_timing',RELAY)
relay=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(relay)

class ControlledClock:
    def __init__(self,now,overshoot=0):
        self.now=now;self.overshoot=overshoot;self.sleeps=[]
    def clock(self):return self.now
    def sleep(self,seconds):
        self.sleeps.append(seconds);self.now+=seconds+self.overshoot

class CopyFaultRelayTimingTests(unittest.TestCase):
    def test_fast_owner_waits_only_until_complete_exchange_target(self):
        clock=ControlledClock(100.2)
        end=relay.wait_for_copy_reply_target(100,clock=clock.clock,sleep=clock.sleep)
        self.assertAlmostEqual(end,104.1)
        self.assertAlmostEqual(sum(clock.sleeps),3.9)
        self.assertTrue(all(0<value<=.02 for value in clock.sleeps))
    def test_slow_real_owner_does_not_receive_an_additional_full_delay(self):
        clock=ControlledClock(102.7)
        end=relay.wait_for_copy_reply_target(100,clock=clock.clock,sleep=clock.sleep)
        self.assertAlmostEqual(end-100,4.1)
        self.assertAlmostEqual(sum(clock.sleeps),1.4)
        self.assertLess(end-100,5)
    def test_valid_owner_after_target_is_forwarded_without_extra_wait(self):
        clock=ControlledClock(104.4)
        self.assertEqual(relay.wait_for_copy_reply_target(100,clock=clock.clock,sleep=clock.sleep),104.4)
        self.assertEqual(clock.sleeps,[])
    def test_owner_at_or_above_rpc_budget_cannot_masquerade_as_valid_control(self):
        for now in (105,105.1,106.8):
            with self.subTest(now=now):
                clock=ControlledClock(now)
                with self.assertRaisesRegex(TimeoutError,'five-second fault-control budget'):
                    relay.wait_for_copy_reply_target(100,clock=clock.clock,sleep=clock.sleep)
                self.assertEqual(clock.sleeps,[])
    def test_scheduler_overshoot_cannot_masquerade_as_valid_control(self):
        clock=ControlledClock(104,overshoot=1.2)
        with self.assertRaisesRegex(TimeoutError,'five-second fault-control budget'):
            relay.wait_for_copy_reply_target(100,clock=clock.clock,sleep=clock.sleep)
        self.assertEqual(len(clock.sleeps),1)

if __name__=='__main__':unittest.main(verbosity=2)
