"""A lost native cleanup reply cannot confirm an issuer revocation barrier."""
import os
from pathlib import Path
import signal
import time
import unittest
import broker_pb2 as broker
import grants_pb2 as grants
from test_workspace_participant import WorkspaceParticipantFixture
import test_workspace_participant_native as native


class NativeSettlementWitness(WorkspaceParticipantFixture):
    native_broker = native.WorkspaceParticipantNativeTests.native_broker

    def workspace_terms(self, *args, **kwargs):
        kwargs.setdefault('rights', [*native.control.access.workspace.RIGHTS,
                                     grants.APPLICATION_OBSERVE, grants.APPLICATION_CONTROL])
        return super().workspace_terms(*args, **kwargs)

    def test_stopped_server_cannot_confirm_native_credential_cleanup(self):
        item, lease, poll, count = self.native_broker()
        released = item.request(control_release=broker.ApplicationControlRelease(id=lease.id, principal=self.principal()))
        self.assertTrue(released.HasField('control_released'), released)
        server = self.native.server
        try:
            server.send_signal(signal.SIGSTOP)
            self.until(lambda: Path(f'/proc/{server.pid}/status').read_text().split('State:', 1)[1].splitlines()[0],
                       lambda value: value.strip().startswith('T'))
            self.revoke_now()
            self.until(lambda: len(poll.poll(0)), lambda value: value == count)
            # Native credential destruction has a real three-second I/O deadline.
            # A stopped server cannot process the revoke, even after all application processes exit.
            deadline = time.monotonic() + 5
            while True:
                status = self.barrier()
                self.assertEqual((status.participant_count, status.pending_count, status.complete), (1, 1, False),
                                 'Broker acknowledged while its native server could not complete credential revocation')
                if item.proc.poll() is not None: break
                if time.monotonic() >= deadline: self.fail('Broker neither failed closed nor retained bounded native cleanup')
                time.sleep(.01)
            self.assertEqual(item.proc.returncode, 2)
            final = self.barrier()
            self.assertEqual((final.participant_count, final.pending_count, final.complete), (1, 1, False),
                             'Broker exit left a successful barrier after unconfirmed native cleanup')
        finally:
            if server.poll() is None: server.send_signal(signal.SIGCONT)




from test_world_bootstrap_races import BootstrapRelay

class NativeSettlementRelay(NativeSettlementWitness):
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
