"""Real issuer approval cannot override participant loss or uncertain local delivery."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import struct
import threading
import time
import unittest
from google.protobuf import text_format
import test_authority_supervisor as existing
import issuer_pb2 as identity
import launch_pb2 as launch
import process_scope_pb2 as process
from harness.wire import frame


class IssuerRelay:
    """Forward actual issuer requests and replies. Never manufacture authority state."""
    def __init__(self, test, after_reply):
        self.test = test; self.after_reply = after_reply
        self.stop = threading.Event(); self.errors = []; self.operations = []
        self.connections = 0; self.stream = None; self.closed = False
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(test.issuer.server_cert), str(test.issuer.server_key))
        context.load_verify_locations(str(test.issuer.service_cert)); context.verify_mode = ssl.CERT_REQUIRED
        self.context = context
        self.listener = socket.socket(); self.listener.bind(('127.0.0.1', 0)); self.listener.listen(4)
        self.listener.settimeout(.1); self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()
        test.addCleanup(self.close)

    def drop(self):
        if self.stream:
            try: self.stream.shutdown(socket.SHUT_RDWR)
            except OSError: pass

    def close(self):
        if self.closed: return
        self.closed = True; self.stop.set()
        self.listener.setblocking(False)
        while True:
            try: pending, _ = self.listener.accept()
            except BlockingIOError: break
            self.connections += 1; pending.close()
        self.listener.close(); self.drop(); self.thread.join(3)
        if self.thread.is_alive(): raise AssertionError('Issuer relay did not stop')
        if self.errors: raise self.errors[0]

    def run(self):
        upstream = None
        try:
            while not self.stop.is_set():
                try: raw, _ = self.listener.accept(); break
                except socket.timeout: continue
            else: return
            self.connections += 1; raw.settimeout(4); self.stream = raw
            self.stream = self.context.wrap_socket(raw, server_side=True)
            upstream = self.test.issuer.connect()
            def exact(size):
                data = b''
                while len(data) < size:
                    part = self.stream.recv(size - len(data))
                    if not part: raise EOFError()
                    data += part
                return data
            while not self.stop.is_set():
                size = struct.unpack('!I', exact(4))[0]
                if size > 1048576: raise AssertionError('Excessive relay request')
                request = identity.IssuerRequest.FromString(exact(size)); sequence = request.request_id
                self.operations.append(request.WhichOneof('operation'))
                reply = upstream.request(request); reply.request_id = sequence
                if self.after_reply(self, request, reply):
                    self.stream.sendall(frame(reply))
        except (OSError, EOFError):
            pass
        except BaseException as error:
            self.errors.append(error)
        finally:
            if self.stream: self.stream.close()
            if upstream: upstream.close()


class ApprovalInterruptionTests(existing.AuthoritySupervisorTests):
    def logs(self):
        return '\n'.join(path.read_text(errors='replace') for path in self.root.glob('supervisor-*.log'))

    def until(self, callback, predicate, message):
        deadline = time.monotonic() + 4
        while True:
            value = callback()
            if predicate(value): return value
            self.assertLess(time.monotonic(), deadline, message)
            time.sleep(.005)

    def approvals(self):
        with sqlite3.connect('file:' + str(self.issuer.state / 'identity.db') + '?mode=ro', uri=True) as db:
            return db.execute('SELECT authorization_id,participant_id,service_incarnation,launch_id,generation '
                'FROM authority_launch_authorization ORDER BY authorization_id').fetchall()

    def interrupted(self, mode):
        self.blocked_read = threading.Event(); self.approval_committed = threading.Event()
        self.approval = None
        def participant_reply(relay, request, reply):
            if mode != 'approval_reply_lost' and request.HasField('revocations') and request.revocations.HasField('after_revision'):
                self.assertTrue(reply.HasField('revocations'), reply)
                self.blocked_read.set()
                relay.stop.wait(4)
                return False
            return True
        self.participant_relay = IssuerRelay(self, participant_reply)
        def approval_reply(relay, request, reply):
            if not request.HasField('authority_launch_complete'): return True
            self.assertTrue(reply.HasField('authority_launch_authorization'), reply)
            self.approval = identity.AuthorityLaunchAuthorization()
            self.approval.CopyFrom(reply.authority_launch_authorization)
            self.approval_committed.set()
            if mode == 'approval_reply_lost':
                relay.drop(); return False
            self.assertTrue(self.blocked_read.wait(2), 'Participant never requested its next revocation read')
            if mode == 'participant_closed': self.participant_relay.drop()
            cause = 'Issuer TLS connection failed' if mode == 'participant_closed' else 'Issuer client deadline exceeded'
            self.until(self.logs, lambda text: 'stage=revocations cause=' + cause in text,
                'Participant did not record the injected interruption')
            return True
        self.approval_relay = IssuerRelay(self, approval_reply)
        config = self.config()
        runtime = identity.AuthorityParticipantRuntimeConfig()
        text_format.Parse(Path(config.participant.runtime_config).read_text(), runtime)
        runtime.issuer_client_config = str(self.issuer.config_file(self.participant_relay.port))
        Path(config.participant.runtime_config).write_text(text_format.MessageToString(runtime))
        # The observer's approval call must outlive the unchanged 700 ms participant deadline.
        config.authorization.issuer_client_config = str(self.issuer.config_file(self.approval_relay.port, io_timeout_ms=3000))
        self.marker = self.root / 'must-not-execute'
        config.launch.CopyFrom(process.AuthorityProcessLaunch(executable='/usr/bin/python3',
            arguments=['-c', 'from pathlib import Path;Path(' + repr(str(self.marker)) + ').touch()']))
        proc, channel = self.supervisor_process(config)
        reply = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(reply.HasField('error'), reply)
        expected = 'Issuer TLS connection failed' if mode == 'approval_reply_lost' else 'Participant failed during supervisor approval'
        self.assertIn(expected, reply.error.message)
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertTrue(self.approval_committed.is_set())
        record = self.checked_cleanup()
        self.assertEqual(reply.error.cleanup_record, record)
        self.assertFalse(record.HasField('authorization'))
        self.assertFalse(self.child_config.exists()); self.assertFalse(self.marker.exists())
        self.assertEqual(proc.stdout.read(), b''); self.assertEqual(self.activation_rows(), [])
        self.assertEqual(self.approval.request.service.service_incarnation, record.binding.service_incarnation)
        self.assertEqual(self.approval.request.launch_id, record.process.launch_id)
        self.assertEqual(self.approval.generation, 0)
        self.assertEqual(self.approvals(), [(self.approval.authorization_id, record.binding.participant_id,
            record.binding.service_incarnation, record.process.launch_id, 0)])
        for relay in (self.participant_relay, self.approval_relay):
            relay.close()
            self.assertEqual(relay.connections, 1)
        self.assertEqual(self.approval_relay.operations.count('authority_launch_begin'), 1)
        self.assertEqual(self.approval_relay.operations.count('authority_launch_complete'), 1)
        self.assertIsNone(self.server.poll())
        target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / (self._testMethodName + '-witness.json.log')).write_text(json.dumps(dict(
                injected_fault=mode, supervisor_error=reply.error.message, issuer_committed=True,
                issuer_generation=self.approval.generation, local_authorization=False, executed=False,
                process_scope_stopped=record.HasField('process_stopped'), native_scope_stopped=record.HasField('native_stopped'),
                participant_requests=self.participant_relay.operations, supervisor_requests=self.approval_relay.operations), indent=2) + '\n')
        return record

    def test_participant_loss_during_committed_approval_stops_scopes_and_allows_fresh_launch(self):
        old = self.interrupted('participant_closed')
        old_path = self.journal / 'launch.db'; old_bytes = old_path.read_bytes()
        self.journal = self.root / 'replay-journal'; self.journal.mkdir(mode=0o700)
        replay, channel = self.supervisor_process(self.config(), start_server=False)
        denied = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(denied.HasField('error'), denied)
        self.assertEqual(replay.wait(timeout=5), 2)
        rejected = self.checked_cleanup()
        self.assertEqual(denied.error.cleanup_record, rejected)
        self.assertFalse(rejected.HasField('authorization'))
        self.assertEqual(rejected.binding.service_incarnation, old.binding.service_incarnation)
        self.assertEqual(len(self.approvals()), 1); self.assertEqual(self.activation_rows(), [])
        self.assertFalse(self.child_config.exists()); self.assertFalse(self.marker.exists())
        self.journal = self.root / 'fresh-journal'; self.journal.mkdir(mode=0o700)
        self.binding.service_incarnation = os.urandom(32)
        proc, channel = self.supervisor_process(self.config(), start_server=False)
        initial = self.message(channel, launch.AuthorityLaunchReply)
        self.assertTrue(initial.HasField('view'), initial)
        self.auth = self.child_update(proc, 'authenticated').authenticated
        self.assertTrue(self.child_update(proc, 'revocations').revocations.initial)
        active = self.activate_child(proc)
        current = self.read_record()
        self.assertEqual(active.generation, 1)
        self.assertNotEqual(current.binding.service_incarnation, old.binding.service_incarnation)
        self.assertNotEqual(current.authorization.authorization_id, self.approval.authorization_id)
        self.assertNotEqual(current.process.launch_id, old.process.launch_id)
        self.assertNotEqual(current.native.credential_id, old.native.credential_id)
        self.assertEqual(old_path.read_bytes(), old_bytes); self.assertFalse(self.marker.exists())
        self.assertEqual(len(self.approvals()), 2)
        self.assertEqual([row for row in self.approvals() if row[0] == self.approval.authorization_id][0][-1], 0)
        self.assertTrue(self.control(channel, fence=launch.AuthorityLaunchQuery()).HasField('view'))
        self.assertEqual(proc.wait(timeout=5), 0); self.checked_cleanup()

    def test_participant_timeout_during_approval_never_executes(self):
        self.interrupted('participant_timeout')

    def test_lost_committed_approval_reply_never_retries_or_executes(self):
        self.interrupted('approval_reply_lost')


if __name__ == '__main__':
    names = ['ApprovalInterruptionTests.' + name for name in ApprovalInterruptionTests.__dict__ if name.startswith('test_')]
    unittest.main(defaultTest=names, verbosity=2)
