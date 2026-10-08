"""Real broker control reservations and unsolicited revocation events."""
import select
import time
import unittest
from unittest.mock import patch
import broker_pb2 as pb
import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
from harness.wire import frame, make_request, result_value
import test_application_access as access
from test_world_identity import WorldIdentityFixture, receive


class LeaseBroker(access.Broker):
    def __init__(self,*args,**kwargs):
        self.events=[]
        super().__init__(*args,**kwargs)

    def read_reply(self):
        reply=receive(self.stream.recv,self.stream,pb.Reply)
        if reply.HasField('control_revoked'):
            self.fixture.assertEqual(reply.protocol_version,1)
            self.fixture.assertEqual(reply.request_id,0)
            self.fixture.assertEqual(len(reply.control_revoked.id),32)
            self.events.append(reply.control_revoked)
        return reply

    def response(self,sequence):
        while True:
            reply=self.read_reply()
            if reply.HasField('control_revoked'):continue
            self.fixture.assertEqual(reply.protocol_version,1)
            self.fixture.assertEqual(reply.request_id,sequence)
            return reply

    def request(self,**operation):
        self.sequence+=1
        self.stream.sendall(frame(pb.Request(protocol_version=1,request_id=self.sequence,**operation)))
        return self.response(self.sequence)

    def call(self,method,**args):
        command=make_request(method,args);command.request_id=self.sequence+1
        reply=self.request(management=command)
        self.fixture.assertFalse(reply.HasField('error'),reply)
        self.fixture.assertFalse(reply.management.HasField('error'),reply)
        return result_value(reply.management)

    def event(self,lease):
        deadline=time.monotonic()+3
        while not any(item.id==lease.id for item in self.events):
            self.fixture.assertTrue(select.select([self.stream],[],[],max(0,deadline-time.monotonic()))[0], 'No idle revocation notification')
            self.fixture.assertTrue(self.read_reply().HasField('control_revoked'))
        event=next(item for item in self.events if item.id==lease.id)
        self.fixture.assertEqual(event,lease)
        return event


class ApplicationControlLeaseTests(WorldIdentityFixture):
    close_brokers=access.ApplicationAccessTests.close_brokers
    launch_application=access.ApplicationAccessTests.launch_application
    finish_application=access.ApplicationAccessTests.finish_application
    data=access.ApplicationAccessTests.data
    principal=access.ApplicationAccessTests.principal
    workspace_terms=access.ApplicationAccessTests.workspace_terms
    enroll_second=access.ApplicationAccessTests.enroll_second
    delegate=access.ApplicationAccessTests.delegate
    revoke_record=access.ApplicationAccessTests.revoke_record
    application_access=access.ApplicationAccessTests.access

    def setUp(self):
        super().setUp();self.brokers=[];self.sequence=0;self.addCleanup(self.close_brokers)

    def application(self,rights=access.ALL,native_enabled=True):
        with patch.object(access,'Broker',LeaseBroker):
            return access.ApplicationAccessTests.setup_application(self,rights,native_enabled=native_enabled)

    def acquire(self,app,claims=None):
        reply=self.broker.request(control_acquire=pb.ApplicationControlRequest(principal=self.principal(claims),application=app))
        self.assertTrue(reply.HasField('control_lease'),reply)
        lease=reply.control_lease;self.assertEqual(len(lease.id),32)
        self.assertEqual(lease.principal,self.principal(claims));self.assertEqual(lease.application,app)
        return lease

    def release(self,lease,claims=None):
        reply=self.broker.request(control_release=pb.ApplicationControlRelease(id=lease.id,principal=self.principal(claims)))
        self.assertTrue(reply.HasField('control_released'),reply)
        self.assertEqual(reply.control_released.id,lease.id)

    def second_controller(self):
        self.enroll_second();app=self.application()
        self.delegate([grant.WORKSPACE_ACCESS,*access.workspace.PROJECT,grant.APPLICATION_OBSERVE])
        record=self.delegate([grant.APPLICATION_CONTROL],app)
        return app,record,self.acquire(app,self.second)

    def test_explicit_control_acquires_live_application(self):
        app=self.application();lease=self.acquire(app);self.release(lease)
        self.assertNotEqual(self.acquire(app).id,lease.id)

    def test_repeated_acquire_preserves_the_same_live_reservation(self):
        app=self.application();lease=self.acquire(app)
        self.assertEqual(self.acquire(app),lease)

    def test_one_application_has_only_one_controller_until_release(self):
        app,record,lease=self.second_controller()
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(principal=self.principal(),application=app)),core.BUSY)
        self.release(lease,self.second);self.acquire(app)

    def test_independent_applications_have_independent_controllers(self):
        app,record,lease=self.second_controller();other=self.launch_application()
        second=self.acquire(other);self.assertNotEqual(second.id,lease.id)

    def test_observation_does_not_grant_control(self):
        self.enroll_second();app=self.application();self.delegate([grant.WORKSPACE_ACCESS,grant.APPLICATION_OBSERVE])
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(principal=self.principal(self.second),application=app)),core.FORBIDDEN)

    def test_readonly_actor_cannot_control_writable_application(self):
        self.enroll_second();app=self.application();self.delegate([*access.READ,*access.APP_RIGHTS])
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(principal=self.principal(self.second),application=app)),core.FORBIDDEN)

    def test_readonly_actor_can_control_readonly_application(self):
        self.enroll_second();self.application();app=self.launch_application('read-only')
        self.delegate([*access.READ,*access.APP_RIGHTS]);self.acquire(app,self.second)

    def test_control_revocation_emits_event_and_keeps_execution(self):
        app,record,lease=self.second_controller();self.revoke_record(record);self.broker.event(lease)
        self.assertEqual(self.application_access(app),[(True,True)])
        self.assertEqual(self.application_access(app,claims=self.second),[(True,False)])
        self.finish_application(app)

    def test_revoked_reservation_blocks_handoff_until_release_acknowledges_cancellation(self):
        app,record,lease=self.second_controller();self.revoke_record(record);self.broker.event(lease)
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(principal=self.principal(),application=app)),core.BUSY)
        self.release(lease,self.second);self.acquire(app)

    def test_other_actor_cannot_release_reservation(self):
        app,record,lease=self.second_controller()
        self.denied(self.broker.request(control_release=pb.ApplicationControlRelease(id=lease.id,principal=self.principal())),core.FORBIDDEN)
        self.release(lease,self.second)

    def test_independent_control_grant_survives_other_grant_revocation(self):
        app,record,lease=self.second_controller();self.delegate([grant.APPLICATION_CONTROL],app)
        self.revoke_record(record);self.assertEqual(self.acquire(app,self.second),lease)
        self.assertFalse(self.broker.events)

    def test_observation_revocation_invalidates_control_reservation(self):
        self.enroll_second();app=self.application()
        self.delegate([grant.WORKSPACE_ACCESS,*access.workspace.PROJECT,grant.APPLICATION_CONTROL])
        record=self.delegate([grant.APPLICATION_OBSERVE],app);lease=self.acquire(app,self.second)
        self.revoke_record(record);self.broker.event(lease)

    def test_actor_revocation_notifies_idle_controller(self):
        app=self.application();lease=self.acquire(app)
        self.revoke(kind=issuer.ACTOR_TARGET);self.broker.event(lease)

    def test_application_exit_notifies_without_an_actor_wait_request(self):
        app=self.application();lease=self.acquire(app)
        (self.data(app)/'release').touch();self.broker.event(lease)

    def test_revoked_reservation_does_not_resurrect_when_permission_returns(self):
        app,record,lease=self.second_controller();self.revoke_record(record);self.broker.event(lease)
        self.delegate([grant.APPLICATION_CONTROL],app)
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(principal=self.principal(self.second),application=app)),core.BUSY)
        self.release(lease,self.second);self.assertNotEqual(self.acquire(app,self.second).id,lease.id)

    def test_unknown_and_malformed_release_cannot_change_reservation(self):
        app=self.application();lease=self.acquire(app)
        self.denied(self.broker.request(control_release=pb.ApplicationControlRelease(id=b'x',principal=self.principal())),core.INVALID_ARGUMENT)
        self.denied(self.broker.request(control_release=pb.ApplicationControlRelease(id=b'x'*32,principal=self.principal())),core.NOT_FOUND)
        self.assertEqual(self.acquire(app),lease)

    def test_actor_revocation_notifies_during_an_unrelated_process_wait(self):
        app,record,lease=self.second_controller()
        self.broker.sequence+=1;sequence=self.broker.sequence
        command=make_request('process.wait',{'id':app.application.decode(),'timeout_seconds':5});command.request_id=sequence
        self.broker.stream.sendall(frame(pb.Request(protocol_version=1,request_id=sequence,management=command)))
        # Allow the normal broker loop to enter the five-second wait.
        self.assertFalse(select.select([self.broker.stream],[],[],.1)[0])
        target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET,id=self.second.actor_id)
        operation=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(target=target,expected_security_revision=0,reason='control lease wait'))
        try:
            reply=self.execute(self.owner_peer,self.challenge(self.owner_peer,operation))
            self.assertTrue(reply.HasField('identity_status'))
            self.broker.event(lease)
        finally:
            (self.data(app)/'release').touch()
        result=self.broker.response(sequence)
        self.assertTrue(result.management.HasField('process_wait'),result)
        self.assertEqual(result.management.process_wait.exit_code,0)

    def test_revoked_actor_can_acknowledge_input_cancellation(self):
        app=self.application();lease=self.acquire(app)
        self.revoke(kind=issuer.ACTOR_TARGET);self.broker.event(lease);self.release(lease)

    def test_revocation_event_is_emitted_once(self):
        app,record,lease=self.second_controller();self.revoke_record(record);self.broker.event(lease)
        for _ in range(3):self.broker.call('system.hello')
        self.assertEqual([item.id for item in self.broker.events],[lease.id])

    def test_workspace_access_revocation_releases_control_without_stopping_execution(self):
        self.enroll_second();app=self.application()
        self.delegate([*access.workspace.PROJECT,*access.APP_RIGHTS])
        record=self.delegate([grant.WORKSPACE_ACCESS]);lease=self.acquire(app,self.second)
        self.revoke_record(record);self.broker.event(lease);self.finish_application(app)

    def test_released_id_cannot_release_a_new_reservation(self):
        app=self.application();old=self.acquire(app);self.release(old);new=self.acquire(app)
        self.denied(self.broker.request(control_release=pb.ApplicationControlRelease(id=old.id,principal=self.principal())),core.NOT_FOUND)
        self.assertEqual(self.acquire(app),new)

    def test_unowned_and_wrongly_attributed_applications_cannot_acquire_control(self):
        app=self.application()
        for field,value in [('server_id',b'x'*32),('application',b'unknown'),('workspace',b'unknown')]:
            other=pb.ApplicationRef();other.CopyFrom(app);setattr(other,field,value)
            self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(principal=self.principal(),application=other)),core.FORBIDDEN)
        self.acquire(app)

    def test_missing_principal_cannot_acquire_control(self):
        app=self.application()
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(application=app)),core.FORBIDDEN)

    def test_nonnative_application_cannot_acquire_control(self):
        app=self.application(native_enabled=False)
        self.denied(self.broker.request(control_acquire=pb.ApplicationControlRequest(principal=self.principal(),application=app)),core.FORBIDDEN)

    def test_partial_private_request_does_not_block_revocation_notification(self):
        app=self.application();lease=self.acquire(app)
        self.broker.sequence+=1;sequence=self.broker.sequence
        command=make_request('system.hello',{});command.request_id=sequence
        payload=frame(pb.Request(protocol_version=1,request_id=sequence,management=command))
        self.broker.stream.sendall(payload[:2])
        self.revoke(kind=issuer.ACTOR_TARGET);self.broker.event(lease)
        self.broker.stream.sendall(payload[2:])
        self.assertTrue(self.broker.response(sequence).management.HasField('hello'))


if __name__=='__main__':unittest.main(defaultTest='ApplicationControlLeaseTests',verbosity=2)
