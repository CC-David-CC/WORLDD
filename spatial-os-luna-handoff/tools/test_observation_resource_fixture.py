"""Generated-protobuf checks for observation fixture resource reservations."""
import unittest

import broker_pb2 as broker
import process_scope_pb2 as process_scope
from observation_resource_fixture import fund_actor_application_reservations


def _budget(memory, swap, processes, cpu, devices):
    value = process_scope.ProcessScopeBudget(memory_bytes=memory, swap_bytes=swap,
        processes=processes, cpu_microseconds=cpu)
    for major, minor, read, write in devices:
        value.io.add(major=major, minor=minor, read_bytes_per_second=read,
                     write_bytes_per_second=write)
    return value


def _policy(workspace, actor_memory=256*2**20, actor_processes=32,
            total_memory=512*2**20, total_processes=64, profile_memory=128*2**20,
            profile_processes=16):
    devices=[(7,20,64*2**20,16*2**20),(259,0,64*2**20,16*2**20)]
    value=broker.WorkspaceResourcePolicy(workspace=workspace)
    value.total.CopyFrom(_budget(total_memory,0,total_processes,200000,devices))
    value.actor_default.CopyFrom(_budget(actor_memory,0,actor_processes,100000,devices))
    for name in ('development','read-only'):
        profile=value.profiles.add(name=name)
        profile.maximum.CopyFrom(_budget(profile_memory,0,profile_processes,100000,devices))
    return value


def _config(with_workspace=True):
    devices=[(7,20,64*2**20,16*2**20),(259,0,64*2**20,16*2**20)]
    value=broker.BrokerResourceConfig(protocol_version=2,delegated_cgroup='/fixture')
    value.broker_budget.CopyFrom(_budget(1024*2**20,0,128,400000,devices))
    value.manager_budget.CopyFrom(_budget(256*2**20,0,32,200000,devices))
    value.default_policy.CopyFrom(_policy(''))
    if with_workspace:
        value.workspaces.add().CopyFrom(_policy('one'))
        value.workspaces.add().CopyFrom(_policy('two',actor_memory=64*2**20,
            actor_processes=8,profile_memory=32*2**20,profile_processes=4))
    return value


def _without_funded_fields(policy):
    value=type(policy)();value.CopyFrom(policy)
    value.actor_default.ClearField('memory_bytes')
    value.actor_default.ClearField('processes')
    return value.SerializeToString(deterministic=True)


class ObservationResourceFixtureTests(unittest.TestCase):
    def test_generic_default_policy_funds_three_applications(self):
        config=_config(with_workspace=False)
        before=_without_funded_fields(config.default_policy)
        result=fund_actor_application_reservations(config,'one',applications=3)
        self.assertEqual(result['policy_workspace'],'')
        self.assertEqual(config.default_policy.actor_default.memory_bytes,384*2**20)
        self.assertEqual(config.default_policy.actor_default.processes,48)
        self.assertEqual(_without_funded_fields(config.default_policy),before)

    def test_explicit_workspace_is_funded_and_unrelated_policies_are_unchanged(self):
        config=_config()
        default_before=config.default_policy.SerializeToString(deterministic=True)
        unrelated_before=config.workspaces[1].SerializeToString(deterministic=True)
        selected_before=broker.WorkspaceResourcePolicy();selected_before.CopyFrom(config.workspaces[0])
        result=fund_actor_application_reservations(config,'one',applications=3)
        self.assertEqual(result['policy_workspace'],'one')
        self.assertEqual(config.workspaces[0].actor_default.memory_bytes,384*2**20)
        self.assertEqual(config.workspaces[0].actor_default.processes,48)
        expected=broker.WorkspaceResourcePolicy();expected.CopyFrom(selected_before)
        expected.actor_default.memory_bytes=384*2**20
        expected.actor_default.processes=48
        self.assertEqual(config.workspaces[0].SerializeToString(deterministic=True),
                         expected.SerializeToString(deterministic=True))
        self.assertEqual(config.default_policy.SerializeToString(deterministic=True),default_before)
        self.assertEqual(config.workspaces[1].SerializeToString(deterministic=True),unrelated_before)

    def test_inadequate_workspace_ceiling_refuses_without_mutation(self):
        config=_config()
        config.workspaces[0].total.memory_bytes=256*2**20
        before=config.SerializeToString(deterministic=True)
        with self.assertRaisesRegex(ValueError,'workspace resource ceiling'):
            fund_actor_application_reservations(config,'one',applications=3)
        self.assertEqual(config.SerializeToString(deterministic=True),before)


if __name__ == '__main__':
    unittest.main()
