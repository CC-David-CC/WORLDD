"""Synthetic checks for retained diagnostics from the healthy issuer fixture."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve()
sys.path.insert(0, str(SOURCE.parent))
original_argv = list(sys.argv)
sys.argv[:] = [sys.argv[0], *(['/bin/true'] * 32)]
try:
    import healthy_issuer_setup as healthy
finally:
    sys.argv[:] = original_argv

import core_pb2 as core
import issuer_pb2 as pb
from test_identity_tls import TlsFixture


class EmptyStream:
    def __init__(self):
        self.timeout = 3.0
        self.sent = b''

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent += data

    def recv(self, size):
        return b''


class DeadDaemon:
    pid = 2147483647

    def poll(self):
        return -9


class HealthyIssuerDiagnosticTests(unittest.TestCase):
    def test_healthy_ready_sets_15000ms_before_inherited_ready(self):
        case = healthy.HealthySupervisorIssuer('runTest')
        case.config = pb.IssuerTlsServerConfig(idle_timeout_ms=3000)
        sentinel = object()
        observed = []

        def inherited_ready(fixture):
            observed.append(fixture.config.idle_timeout_ms)
            return sentinel

        with patch.object(TlsFixture, 'ready', inherited_ready):
            self.assertIs(case.ready(), sentinel)
        self.assertEqual(observed, [healthy.HEALTHY_ISSUER_IDLE_TIMEOUT_MS])
        self.assertEqual(healthy.HEALTHY_ISSUER_IDLE_TIMEOUT_MS, 15000)

    def test_eof_capture_keeps_stage_type_status_and_original_error(self):
        stream = EmptyStream()
        observations = []
        peer = healthy.DeadlinePeer(stream, record=lambda *args: observations.append(args),
                            failure=lambda request, stage, started, idle, error:
                                observations.append((stage, request.WhichOneof('operation'),
                                                    error, idle)))
        request = pb.IssuerRequest(protocol_version=1, hello=core.HelloRequest())
        with self.assertRaisesRegex(EOFError, 'Issuer TLS stream closed') as raised:
            peer.request(request)
        self.assertEqual(stream.timeout, 3.0)
        self.assertEqual(observations[0][0:2], ('reply_header', 'hello'))
        self.assertIs(observations[0][2], raised.exception)
        self.assertGreaterEqual(observations[0][3], 0.0)

    def test_capture_failure_preserves_original_eof_and_timeout(self):
        stream = EmptyStream()
        errors = []
        def broken_capture(request, stage, started, idle, error):
            errors.append(error)
            raise RuntimeError('diagnostic marker')
        peer = healthy.DeadlinePeer(stream, failure=broken_capture)
        request = pb.IssuerRequest(protocol_version=1, hello=core.HelloRequest())
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaisesRegex(EOFError, 'Issuer TLS stream closed') as raised:
                peer.request(request)
        self.assertEqual(stream.timeout, 3.0)
        self.assertIs(errors[0], raised.exception)
        self.assertIn('diagnostic capture failed: RuntimeError', captured.getvalue())
        self.assertNotIn('diagnostic marker', captured.getvalue())

    def test_failure_record_keeps_only_allowlisted_stderr_markers(self):
        case = healthy.HealthySupervisorIssuer('runTest')
        case._testMethodName = 'test_failure_record'
        case.config = type('Config', (), {'idle_timeout_ms': 3000})()
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'issuer.log'
            log.write_text('identityd: bearer_token=must-not-escape\n'
                           'issuer TLS loop delayed monotonic_us=12 poll_ms=3 work_ms=4\n'
                           'issuer TLS loop delayed monotonic_us=12 poll_ms=3 work_ms=4 bearer_token=must-not-escape\n')
            case.daemons = [(DeadDaemon(), type('Log', (), {'name': str(log)})())]
            request = pb.IssuerRequest(
                protocol_version=1,
                challenge=pb.IssuerOperation(
                    participant_register=pb.AuthorityParticipantRegister(
                        participant=pb.AuthorityParticipant(id=b'must-not-escape',
                                                            public_key=b'secret-public-key'))))
            with tempfile.TemporaryDirectory() as evidence:
                previous = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
                os.environ['SPATIAL_SECURITY_EVIDENCE'] = evidence
                try:
                    captured = io.StringIO()
                    with contextlib.redirect_stderr(captured):
                        case.capture_request_failure(request, 'reply_header',
                                                     123.0, 3.2, EOFError())
                    path = Path(evidence) / 'test_failure_record-issuer-failure.json'
                    record = json.loads(path.read_text())
                    output_text = path.read_text()
                finally:
                    if previous is None:
                        os.environ.pop('SPATIAL_SECURITY_EVIDENCE', None)
                    else:
                        os.environ['SPATIAL_SECURITY_EVIDENCE'] = previous
            self.assertEqual(record['operation_type'], 'challenge')
            self.assertEqual(record['nested_operation_type'], 'participant_register')
            self.assertEqual(record['stage'], 'reply_header')
            self.assertEqual(record['elapsed_since_last_success_seconds'], 3.2)
            daemon = record['daemons'][0]
            self.assertEqual(daemon['pid'], DeadDaemon.pid)
            self.assertEqual(daemon['returncode'], -9)
            markers = daemon['stderr_markers']
            self.assertTrue(any(marker.get('present') for marker in markers
                                if marker['marker'] == 'identityd_error_present'))
            self.assertIn({'marker': 'issuer_tls_loop_delayed', 'monotonic_us': 12,
                           'poll_ms': 3, 'work_ms': 4}, markers)
            self.assertNotIn('must-not-escape', output_text)
            self.assertNotIn('must-not-escape', captured.getvalue())
            self.assertNotIn('secret-public-key', output_text)
            self.assertNotIn('secret-public-key', captured.getvalue())
            self.assertEqual(len(markers), 2)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    if suite.countTestCases() != 4:
        raise AssertionError('Expected exactly four synthetic fixture checks')
    print('Discovered exactly 4 synthetic fixture checks', flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
