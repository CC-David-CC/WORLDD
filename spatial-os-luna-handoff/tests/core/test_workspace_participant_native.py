"""Native broker obligations wait for release of real control reservations."""
import os
import socket
import types
import unittest
from unittest.mock import patch
import broker_pb2 as broker
import grants_pb2 as grants
import security_pb2 as security
from test_workspace_participant import WorkspaceParticipantFixture,BrokerProcess
import test_application_control_lease as control

class NativeBroker(control.LeaseBroker):
    def __init__(self, fixture, config, native_config=None, control_transport=None):
        workspace = control.access.workspace
        workspace.initialize_broker_lifecycle(
            self, fixture, f'broker-{len(fixture.brokers)}.log')
        try:
            super().__init__(fixture, config, native_config, control_transport)
        except BaseException as error:
            try:
                workspace.BrokerProcess.close(self)
            except BaseException as cleanup:
                error.add_note('Partial native broker cleanup: '+repr(cleanup))
            raise

    close=BrokerProcess.close

class WorkspaceParticipantNativeTests(WorkspaceParticipantFixture):
    def test_native_broker_partial_initialization_closes_full_lifecycle(self):
        fixture = types.SimpleNamespace(root=self.root/'native-lifecycle', brokers=[])
        fixture.root.mkdir()
        created = []
        peers = []

        def fail_after_partial_setup(instance, fixture, config, native_config=None, control_transport=None):
            self.assertFalse(instance.closed)
            self.assertIsNone(instance.resources)
            self.assertIsNone(instance.proc)
            self.assertIsNone(instance.owner_log)
            self.assertIsNone(instance.storage_owner)
            self.assertIsNone(getattr(instance, 'storage_owner_launch_path', None))
            self.assertIsNone(instance.storage_owner_log)
            self.assertIsNone(instance.storage_control)
            self.assertIsNone(instance.storage_gate_write)
            self.assertIsNone(instance.storage_unit)
            self.assertIsNone(instance.storage_cleanup_confirmed)
            self.assertEqual(instance.channels, [])
            self.assertEqual(instance.log_path, fixture.root/'broker-0.log')
            instance.stream, peer = socket.socketpair()
            peers.append(peer)
            instance.log_path.parent.mkdir(exist_ok=True)
            instance.log = instance.log_path.open('wb')
            instance.path = instance.log_path
            instance.fixture = fixture
            created.append(instance)
            raise RuntimeError('injected native broker initialization failure')

        with patch.object(control.LeaseBroker, '__init__', fail_after_partial_setup):
            with self.assertRaisesRegex(RuntimeError, 'injected native broker initialization failure'):
                NativeBroker(fixture, self.root/'broker.textproto')

        self.assertEqual(len(created), 1)
        item = created[0]
        self.assertTrue(item.closed)
        self.assertEqual(item.stream.fileno(), -1)
        self.assertTrue(item.log.closed)
        item.close()
        control.access.workspace.BrokerProcess.close(item)
        self.assertTrue(item.closed)
        for peer in peers:
            peer.close()

    def workspace_terms(self,*args,**kwargs):
        kwargs.setdefault('rights',[*control.access.workspace.RIGHTS,grants.APPLICATION_OBSERVE,grants.APPLICATION_CONTROL])
        return super().workspace_terms(*args,**kwargs)
    def native_broker(self):
        self.native=control.access.native.SecurityIdentityTests(methodName='runTest')
        self.native._testMethodName=self._testMethodName;self.native.setUp();self.addCleanup(self.native.doCleanups)
        root=self.native.start();self.server_id=root.inspect().server_id
        config=broker.NativeLaunchConfig(protocol_version=1,display_number=self.native.display,
            broker_cookie=root.issue(security.BROKER,b'',b'').cookie,server_id=self.server_id,
            server_pid=self.native.server.pid,server_uid=os.getuid())
        item=self.start_broker(factory=lambda fixture,state,path:NativeBroker(fixture,path,config))
        self.addCleanup(item.close)
        poll,count=self.launch_application()
        app=broker.ApplicationRef(workspace=b'one',application=self.application.encode(),server_id=self.server_id)
        result=item.request(control_acquire=broker.ApplicationControlRequest(principal=self.principal(),application=app))
        self.assertTrue(result.HasField('control_lease'),result)
        return item,result.control_lease,poll,count
    def test_process_exit_does_not_acknowledge_unreleased_control(self):
        item,lease,poll,count=self.native_broker();self.revoke_now();item.event(lease)
        self.until(lambda:len(poll.poll(0)),lambda value:value==count)
        pending=self.barrier();self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False))
        result=item.request(control_release=broker.ApplicationControlRelease(id=lease.id,principal=self.principal()))
        self.assertTrue(result.HasField('control_released'),result)
        completed=self.until(self.barrier,lambda value:value.complete);self.assertEqual(completed.participant_count,1)
        item.close();self.assertEqual(item.proc.returncode,0);self.assertEqual(self.activations(),[(1,1,0)])
    def test_shutdown_with_unreleased_control_preserves_obligation(self):
        item,lease,poll,count=self.native_broker();self.revoke_now();item.event(lease)
        self.until(lambda:len(poll.poll(0)),lambda value:value==count)
        item.close();self.assertEqual(item.proc.returncode,2)
        pending=self.barrier();self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False))
        self.assertEqual(self.activations(),[(1,0,1)])

if __name__=='__main__':unittest.main(verbosity=2)
