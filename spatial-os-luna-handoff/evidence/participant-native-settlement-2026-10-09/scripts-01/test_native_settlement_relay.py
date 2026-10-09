"""Delay a genuine keepalive reply across actor revocation and native failure."""
import unittest
import test_native_settlement_witness as baseline
from test_workspace_participant import WorkspaceParticipantFixture
from test_world_bootstrap_races import BootstrapRelay


class NativeSettlementRelay(baseline.NativeSettlementWitness):
    def start_broker(self, *args, **kwargs):
        kwargs.update(relay=self.relay, query_relay=self.relay)
        return super().start_broker(*args, **kwargs)

    def native_broker(self):
        self.relay = BootstrapRelay(self, lambda: WorkspaceParticipantFixture.revoke_now(self), 'info')
        return super().native_broker()

    def revoke_now(self):
        self.relay.armed.set()
        self.assertTrue(self.relay.delivered.wait(2), 'Keepalive did not deliver the real actor revocation')


if __name__ == '__main__': unittest.main(verbosity=2)
