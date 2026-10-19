"""Focused regressions for inherited storage-fixture I/O bindings."""
import unittest
from types import SimpleNamespace
from storage_session_resources import add_loop_binding
import broker_pb2 as broker
import process_scope_pb2 as process

BUDGET = "spatial.supervisor.v1.ProcessScopeBudget"


class IoEntry:
    def __init__(self, major, minor, read_rate, write_rate):
        self.major, self.minor = major, minor
        self.read_bytes_per_second = read_rate
        self.write_bytes_per_second = write_rate

    def CopyFrom(self, other):
        self.major, self.minor = other.major, other.minor
        self.read_bytes_per_second = other.read_bytes_per_second
        self.write_bytes_per_second = other.write_bytes_per_second


class IoList(list):
    def add(self):
        entry = IoEntry(0, 0, 0, 0)
        self.append(entry)
        return entry


class Budget:
    def __init__(self, *entries):
        self.DESCRIPTOR = SimpleNamespace(full_name=BUDGET)
        self.io = IoList(entries)


class StorageSessionResourceTests(unittest.TestCase):
    def test_single_backing_binding_is_cloned_for_fixture_loop(self):
        backing = IoEntry(8, 16, 70, 20)
        budget = Budget(backing)
        add_loop_binding(budget, BUDGET, (8, 16), (7, 3))
        self.assertEqual([(io.major, io.minor) for io in budget.io], [(8, 16), (7, 3)])
        self.assertEqual((budget.io[1].read_bytes_per_second, budget.io[1].write_bytes_per_second), (70, 20))
        self.assertEqual((backing.major, backing.minor), (8, 16))

    def test_multiple_existing_devices_and_ceilings_are_preserved(self):
        backing = IoEntry(8, 16, 70, 20)
        other = IoEntry(259, 4, 31, 11)
        budget = Budget(backing, other)
        add_loop_binding(budget, BUDGET, (8, 16), (7, 3))
        self.assertEqual([(io.major, io.minor) for io in budget.io], [(8, 16), (259, 4), (7, 3)])
        self.assertEqual([(io.read_bytes_per_second, io.write_bytes_per_second) for io in budget.io],
                         [(70, 20), (31, 11), (70, 20)])

    def test_generated_broker_resource_tree_preserves_all_device_limits(self):
        backing_device, other_device, loop_device = (259, 4), (8, 16), (7, 3)
        resources = broker.BrokerResourceConfig()
        expected_budgets = []

        def add_budget(target, backing_rate, other_rate):
            budget = process.ProcessScopeBudget(memory_bytes=64 * 1024 * 1024)
            budget.io.add(major=backing_device[0], minor=backing_device[1],
                          read_bytes_per_second=backing_rate,
                          write_bytes_per_second=backing_rate // 2)
            budget.io.add(major=other_device[0], minor=other_device[1],
                          read_bytes_per_second=other_rate,
                          write_bytes_per_second=other_rate // 2)
            target.CopyFrom(budget)
            expected_budgets.append((target, backing_rate, other_rate))

        add_budget(resources.broker_budget, 120, 60)
        add_budget(resources.manager_budget, 90, 45)
        add_budget(resources.default_policy.total, 80, 40)
        add_budget(resources.default_policy.actor_default, 70, 35)
        add_budget(resources.default_policy.profiles.add(name="development").maximum, 60, 30)
        add_budget(resources.workspaces.add(workspace="fixture").total, 50, 25)
        add_budget(resources.copy_profiles.add(name="copy").policy.total, 40, 20)

        add_loop_binding(resources, process.ProcessScopeBudget.DESCRIPTOR.full_name,
                         backing_device, loop_device)

        for budget, backing_rate, other_rate in expected_budgets:
            self.assertEqual([(io.major, io.minor) for io in budget.io],
                             [backing_device, other_device, loop_device])
            self.assertEqual([(io.read_bytes_per_second, io.write_bytes_per_second)
                              for io in budget.io],
                             [(backing_rate, backing_rate // 2),
                              (other_rate, other_rate // 2),
                              (backing_rate, backing_rate // 2)])

    def test_duplicate_device_bindings_are_rejected_without_mutation(self):
        budget = Budget(IoEntry(8, 16, 70, 20), IoEntry(8, 16, 60, 15))
        with self.assertRaisesRegex(AssertionError, "unique"):
            add_loop_binding(budget, BUDGET, (8, 16), (7, 3))
        self.assertEqual(len(budget.io), 2)

    def test_missing_backing_and_prebound_loop_are_rejected(self):
        missing = Budget(IoEntry(259, 4, 31, 11))
        with self.assertRaisesRegex(AssertionError, "backing device"):
            add_loop_binding(missing, BUDGET, (8, 16), (7, 3))
        prebound = Budget(IoEntry(8, 16, 70, 20), IoEntry(7, 3, 70, 20))
        with self.assertRaisesRegex(AssertionError, "already bound"):
            add_loop_binding(prebound, BUDGET, (8, 16), (7, 3))


if __name__ == "__main__":
    unittest.main()
