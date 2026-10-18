"""The real supervisor must connect its owned launch to issuer fencing."""
import struct
import socket
import ssl
import threading
from pathlib import Path
import sys
import unittest
from google.protobuf import text_format
SOURCE = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0,str(SOURCE/'tests/native'))
import core_pb2 as core
import test_authority_supervisor as supervisor
import launch_pb2 as launch
from harness.wire import frame


class LostActivationReplyRelay:
    """Commit one real issuer activation, then drop its reply to create UNKNOWN at the sender."""
    def __init__(self, owner, expected_service_incarnation, typed_storage_error=False):
        self.owner = owner
        self.fixture = owner.issuer
        self.expected_service_incarnation = expected_service_incarnation
        self.typed_storage_error = typed_storage_error
        self.stop_event = threading.Event()
        self.activation_committed = threading.Event()
        self.errors = []
        self.stream = None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(self.fixture.server_cert), str(self.fixture.server_key))
        context.load_verify_locations(str(self.fixture.service_cert))
        context.verify_mode = ssl.CERT_REQUIRED
        self.context = context
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0)); self.listener.listen(4); self.listener.settimeout(.1)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()
        owner.addCleanup(self.close)

    def run(self):
        upstream = None
        try:
            while not self.stop_event.is_set():
                try: raw, _ = self.listener.accept(); break
                except socket.timeout: continue
            else: return
            raw.settimeout(3)
            self.stream = self.context.wrap_socket(raw, server_side=True)
            upstream = self.fixture.connect()
            def exact(size):
                data = bytearray()
                while len(data) < size:
                    part = self.stream.recv(size - len(data))
                    if not part: raise EOFError()
                    data.extend(part)
                return bytes(data)
            while not self.stop_event.is_set():
                size = struct.unpack('!I', exact(4))[0]
                if size > 1048576: raise AssertionError('Excessive issuer relay frame')
                request = identity.IssuerRequest.FromString(exact(size))
                request_id = request.request_id
                reply = upstream.request(request)
                reply.request_id = request_id
                if request.HasField('authority_activate'):
                    if not (reply.HasField('authority_activation') and reply.authority_activation.active and
                            reply.authority_activation.service_incarnation == self.expected_service_incarnation and
                            reply.request_id == request_id):
                        raise AssertionError('Upstream activation did not return the exact active service generation')
                    self.activation_committed.set()
                    if self.typed_storage_error:
                        reply.Clear(); reply.protocol_version = 1; reply.request_id = request_id
                        reply.error.code = core.STORAGE_ERROR
                        reply.error.message = 'Injected typed failure after committed activation'
                        self.stream.sendall(frame(reply))
                    return
                self.stream.sendall(frame(reply))
        except (OSError, EOFError):
            pass
        except BaseException as error:
            self.errors.append(error)
        finally:
            if self.stream: self.stream.close()
            if upstream: upstream.close()

    def close(self):
        self.stop_event.set(); self.listener.close()
        if self.stream:
            try: self.stream.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        self.thread.join(3)
        if self.thread.is_alive(): raise AssertionError('Issuer relay did not stop')
        if self.errors: raise self.errors[0]


class SupervisorFencingBaselineTests(supervisor.AuthoritySupervisorTests):
    def test_supervisor_fences_its_actual_activated_child_before_checked_cleanup(self):
        proc, channel = self.running()
        active = self.activate_child(proc)
        before = self.read_record()
        # Additive protobuf field 20 is an empty AuthorityLaunchQuery.
        # This encodes the same request against old and new generated bindings.
        request = launch.AuthorityLaunchRequest(protocol_version=1,request_id=77).SerializeToString()+b'\xa2\x01\x00'
        channel.sendall(struct.pack('!I',len(request))+request)
        reply = self.message(channel,launch.AuthorityLaunchReply)
        self.assertEqual(reply.request_id,77)
        self.assertFalse(reply.HasField('error'),'Missing production supervisor fencing: '+str(reply))
        self.assertTrue(reply.HasField('view'),reply)
        record = reply.view.record
        self.assertEqual(record.stage,launch.OWNED_SCOPES_STOPPED)
        self.assertEqual(proc.wait(timeout=5),0)
        self.assertEqual(self.checked_cleanup(),record)
        self.assertTrue(record.HasField('fence'))
        self.assertEqual(record.fence.generation,active.generation)
        self.assertEqual(record.fence.launch_id,before.process.launch_id)
        self.assertEqual(record.fence.authorization_id,before.authorization.authorization_id)
        self.assertEqual(record.fence.registration,self.issuer.registration)
        self.assertEqual(record.fence.service_incarnation,before.binding.service_incarnation)
        self.assertEqual(record.fence.supervisor_public_key,self.issuer.supervisor_public)
        self.assertEqual(record.authorization,before.authorization)
        rows = self.issuer.snapshot_all()
        self.assertEqual(len(rows['authority_fence']),1)
        self.assertEqual(len(rows['authority_activation']),1)
        self.assertEqual(rows['authority_activation'][0][-1],1)


class SupervisorStopBeforeActivationTests(supervisor.AuthoritySupervisorTests):
    def _assert_ambiguous_activation_stays_pending(self, typed_storage_error=False):
        config = self.config()
        runtime_path = Path(config.participant.runtime_config)
        runtime = text_format.Parse(runtime_path.read_text(), identity.AuthorityParticipantRuntimeConfig())
        relay_peer = LostActivationReplyRelay(self, runtime.service_incarnation, typed_storage_error=typed_storage_error)
        runtime.issuer_client_config = str(self.healthy_client_config(endpoint_port=relay_peer.port))
        runtime_path.write_text(text_format.MessageToString(runtime))
        proc, channel = self.running(config)
        self.command(1, activate=identity.AuthorityActivate(synchronized_revision=0))
        failed = self.child_update(proc, 'error')
        self.assertEqual(failed.request_id, 1)
        if typed_storage_error:
            self.assertEqual(failed.error.code, core.STORAGE_ERROR)
        self.assertTrue(relay_peer.activation_committed.wait(2), 'Issuer activation did not commit before reply handling')
        self.issuer.supervisor_key.write_bytes(self.issuer.signer.read_bytes())
        reply = self.control(channel, stop=launch.AuthorityLaunchQuery())
        self.assertEqual(reply.request_id, 77)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertFalse(reply.HasField('view'), reply)
        self.assertFalse(reply.error.HasField('cleanup_record'), reply)
        self.assertIn('Fence signing key differs from owned authorization', reply.error.message)
        self.assertIsNone(proc.poll(), 'Pending supervisor must retain explicit retry ownership')
        record = self.read_record()
        self.assertEqual(record.stage, launch.CLEANUP_PENDING)
        self.assertTrue(record.HasField('process_stopped'))
        self.assertTrue(record.HasField('native_stopped'))
        self.assertFalse(record.HasField('fence'))
        # The initial local cleanup and two retries consume the same three-attempt budget.
        for _ in range(2):
            retry = self.control(channel, stop=launch.AuthorityLaunchQuery())
            self.assertTrue(retry.HasField('error'), retry)
            self.assertIn('Fence signing key differs from owned authorization', retry.error.message)
            self.assertFalse(retry.error.HasField('cleanup_record'), retry)
            self.assertEqual(self.read_record(), record)
        exhausted = self.control(channel, stop=launch.AuthorityLaunchQuery())
        self.assertTrue(exhausted.HasField('error'), exhausted)
        self.assertIn('Cleanup retry budget exhausted', exhausted.error.message)
        self.assertFalse(exhausted.error.HasField('cleanup_record'), exhausted)
        self.assertIsNone(proc.poll(), 'Budget exhaustion must retain pending ownership')
        self.assertEqual(self.read_record(), record)
        rows = self.issuer.snapshot_all()
        self.assertEqual(len(rows['authority_activation']), 1)
        self.assertEqual(rows['authority_activation'][0][-1], 1)
        self.assertEqual(rows['authority_fence'], [])
        self.assertEqual(rows['authority_retirement'], [])
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=8), 2)
        self.assertEqual(self.read_record(), record)

    def test_lost_activation_reply_requires_strict_fence_and_keeps_pending_without_receipt(self):
        self._assert_ambiguous_activation_stays_pending()

    def test_typed_storage_error_after_commit_is_ambiguous_and_requires_strict_fence(self):
        self._assert_ambiguous_activation_stays_pending(typed_storage_error=True)


import sqlite3
import signal
import issuer_pb2 as identity
import security_pb2 as security


class SupervisorFenceFailures(supervisor.AuthoritySupervisorTests):
    def assert_pending_cleanup_retry(self,proc,channel,reply):
        self.assertTrue(reply.HasField('error'),reply)
        self.assertFalse(reply.error.HasField('cleanup_record'),reply)
        self.assertIsNone(proc.poll(),'Failed cleanup must retain supervisor ownership')

        inspect=self.control(channel,inspect=launch.AuthorityLaunchQuery())
        self.assertEqual(inspect.request_id,77)
        self.assertTrue(inspect.HasField('error'),inspect)
        self.assertFalse(inspect.HasField('view'),inspect)
        self.assertFalse(inspect.error.HasField('cleanup_record'),inspect)
        self.assertIn('only an explicit stop retry is accepted',inspect.error.message)
        self.assertIsNone(proc.poll(),'Rejected inspection must leave pending owner alive')

        retry=self.control(channel,stop=launch.AuthorityLaunchQuery())
        self.assertEqual(retry.request_id,77)
        self.assertTrue(retry.HasField('error'),retry)
        self.assertFalse(retry.HasField('view'),retry)
        self.assertFalse(retry.error.HasField('cleanup_record'),retry)
        self.assertIsNone(proc.poll(),'Failed explicit retry must retain pending owner')

    def failed_fence(self,channel):
        request=launch.AuthorityLaunchRequest(protocol_version=1,request_id=77,fence=launch.AuthorityLaunchQuery()).SerializeToString()
        channel.sendall(struct.pack('!I',len(request))+request)
        reply=self.message(channel,launch.AuthorityLaunchReply)
        self.assertEqual(reply.request_id,77) # A decoded stop/fence request keeps its correlation.
        self.assertTrue(reply.HasField('error'),reply)
        return reply

    def test_pending_revocation_survives_fenced_cleanup(self):
        proc,channel=self.running();self.activate_child(proc)
        self.revoke();self.child_update(proc,'revocations')
        self.assertEqual(self.barrier().pending_count,1)
        reply=self.control(channel,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('view'),reply)
        self.assertEqual(proc.wait(timeout=5),0)
        record=self.checked_cleanup();self.assertTrue(record.HasField('fence'))
        self.assertEqual(record.fence.security_revision,1)
        pending=self.barrier()
        self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False))

    def test_unconsumed_authorization_cannot_be_fenced(self):
        proc,channel=self.running()
        reply=self.failed_fence(channel)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(proc.wait(timeout=5),2)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        rows=self.issuer.snapshot_all()
        self.assertEqual(rows['authority_fence'],[]);self.assertEqual(rows['authority_activation'],[])

    def test_replaced_signing_key_cannot_fence_owned_launch(self):
        proc,channel=self.running();self.activate_child(proc)
        self.issuer.supervisor_key.write_bytes(self.issuer.signer.read_bytes())
        reply=self.failed_fence(channel)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertIn('Fence signing key differs',reply.error.message)
        self.assertEqual(proc.wait(timeout=5),2)
        self.assertFalse(self.checked_cleanup().HasField('fence'))
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'],[])

    def test_native_cleanup_uncertainty_preserves_committed_fence(self):
        proc,channel=self.running();self.activate_child(proc)
        before=self.read_record()
        revoked=self.supervisor.call(revoke=security.Revoke(credential_id=before.native.credential_id))
        self.assertEqual(revoked.code,security.OK);self.assertTrue(revoked.revoked)
        reply=self.failed_fence(channel)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertFalse(reply.error.HasField('cleanup_record'),reply)
        self.assert_pending_cleanup_retry(proc,channel,reply)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=8),2)
        record=self.read_record()
        self.assertEqual(record.stage,launch.CLEANUP_PENDING)
        self.assertTrue(record.HasField('fence'));self.assertTrue(record.HasField('process_stopped'))
        self.assertFalse(record.HasField('native_stopped'))
        self.assertEqual(len(self.issuer.snapshot_all()['authority_fence']),1)

    def test_failed_local_commit_never_reports_success(self):
        proc,channel=self.running();self.activate_child(proc)
        before=self.read_record()
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute("CREATE TRIGGER refuse_fence BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'fence journal fault'); END")
        reply=self.failed_fence(channel)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertFalse(reply.error.HasField('cleanup_record'),reply)
        self.assert_pending_cleanup_retry(proc,channel,reply)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=8),2)
        self.assertEqual(self.read_record(),before)
        self.assertEqual(len(self.issuer.snapshot_all()['authority_fence']),1)


class CoordinatorFenceTests(supervisor.owned.OwnedParticipantTests):
    def live_authorized(self):
        proc,view,auth,batch=self.connected()
        self.assertTrue(self.authorize(proc).HasField('view'))
        self.assertTrue(self.run_child(proc,'import time;time.sleep(30)').HasField('view'))
        self.assertTrue(self.activate(proc,batch.through_revision).HasField('view'))
        self.update(proc,'activated')
        return proc

    def test_matching_fence_retry_preserves_record_and_rejects_further_participant_commands(self):
        proc=self.live_authorized()
        reply=self.call(proc,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('view'),reply);record=reply.view.record
        self.assertTrue(record.HasField('fence'))
        self.assertEqual(self.call(proc,fence=launch.AuthorityLaunchQuery()).view.record,record)
        self.assertTrue(self.activate(proc).HasField('error'))
        stopped=self.stopped(proc)
        self.assertEqual(stopped.fence,record.fence)
        self.assertEqual(self.call(proc,fence=launch.AuthorityLaunchQuery()).view.record,stopped)

    def test_lost_local_commit_can_retry_exact_remote_fence_with_retained_ownership(self):
        proc=self.live_authorized();before=self.read_record()
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute("CREATE TRIGGER refuse_fence BEFORE UPDATE ON launch BEGIN SELECT RAISE(ABORT,'fence journal fault'); END")
        reply=self.call(proc,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(self.read_record(),before)
        rows=self.issuer.snapshot_all()['authority_fence'];self.assertEqual(len(rows),1)
        with sqlite3.connect(self.journal/'launch.db') as db:db.execute('DROP TRIGGER refuse_fence')
        retry=self.call(proc,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(retry.HasField('view'),retry)
        self.assertTrue(retry.view.record.HasField('fence'))
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'],rows)
        self.stopped(proc)

    def test_released_generation_cannot_be_fenced(self):
        proc=self.live_authorized()
        reply=self.call(proc,participant_command=identity.ParticipantCommand(request_id=72,
            release=identity.AuthorityRelease(generation=1,applied_revision=0)))
        self.assertTrue(reply.HasField('view'),reply);self.update(proc,'released')
        reply=self.call(proc,fence=launch.AuthorityLaunchQuery())
        self.assertTrue(reply.HasField('error'),reply)
        self.assertFalse(self.read_record().HasField('fence'))
        self.assertEqual(self.issuer.snapshot_all()['authority_fence'],[])
        self.stopped(proc)

    def test_reopened_journal_rejects_mismatched_fence_and_cannot_reconstruct_ownership(self):
        proc=self.live_authorized()
        self.assertTrue(self.call(proc,fence=launch.AuthorityLaunchQuery()).HasField('view'))
        record=self.stopped(proc)
        proc.stdin.close();self.assertEqual(proc.wait(timeout=5),0)
        for field,value in [('issuer_id',b'x'*32),('generation',2),('service_incarnation',b'x'*32),
                ('authorization_id',b'x'*32),('launch_id',b'x'*32),('supervision_revision',2),
                ('supervisor_public_key',b'x'*32),('fence_id',b''),('security_revision',2**63)]:
            changed=launch.AuthorityLaunchRecord();changed.CopyFrom(record)
            setattr(changed.fence,field,value)
            with sqlite3.connect(self.journal/'launch.db') as db:
                db.execute('UPDATE launch SET record=?',(changed.SerializeToString(),))
            rejected=self.probe(start_server=False);self.assertEqual(rejected.wait(timeout=5),2)
        with sqlite3.connect(self.journal/'launch.db') as db:
            db.execute('UPDATE launch SET record=?',(record.SerializeToString(),))
        reopened=self.probe(start_server=False)
        self.assertEqual(self.call(reopened,inspect=launch.AuthorityLaunchQuery()).view.record,record)
        self.assertTrue(self.call(reopened,fence=launch.AuthorityLaunchQuery()).HasField('error'))
        self.assertTrue(self.call(reopened,prepare=self.binding).HasField('error'))


if __name__ == '__main__':
    suite=unittest.TestSuite(cls(name) for cls in
        (SupervisorFencingBaselineTests,SupervisorFenceFailures,SupervisorStopBeforeActivationTests,CoordinatorFenceTests)
        for name in cls.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
