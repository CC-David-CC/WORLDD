"""Bounded healthy supervisor setup; fault fixtures retain their own deadlines."""
import json
import os
import re
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

import issuer_pb2 as pb
from harness.wire import frame
from test_identity_issuer import BINARY
from test_identity_tls import Peer, TlsFixture
from test_supervisor_registry import SupervisorRegistryTests


class DeadlinePeer(Peer):
    def __init__(self, stream, timeout=5, record=None, failure=None, idle_timeout_ms=None):
        super().__init__(stream)
        self.timeout = timeout
        self.record = record
        self.failure = failure
        self.idle_timeout_ms = idle_timeout_ms
        self.last_success_at = time.monotonic()

    def request(self, request):
        started = time.monotonic()
        elapsed_since_last_success = max(0.0, started - self.last_success_at)
        deadline = started + self.timeout
        previous_timeout = self.stream.gettimeout()
        outcome = 'failed'
        stage = 'request_write'

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError('Healthy issuer request deadline expired')
            self.stream.settimeout(value)

        def exact(size):
            data = bytearray()
            while len(data) < size:
                remaining()
                part = self.stream.recv(size - len(data))
                if not part:
                    raise EOFError('Issuer TLS stream closed')
                data.extend(part)
            return bytes(data)

        try:
            self.sequence += 1
            request.request_id = self.sequence
            remaining()
            self.stream.sendall(frame(request))
            stage = 'reply_header'
            length = struct.unpack('!I', exact(4))[0]
            if length > 1048576:
                raise AssertionError('Excessive issuer reply')
            stage = 'reply_body'
            reply = pb.IssuerReply.FromString(exact(length))
            if reply.protocol_version != 1 or reply.request_id != self.sequence:
                raise AssertionError('Wrong issuer reply identity')
            outcome = 'received'
            self.last_success_at = time.monotonic()
            return reply
        except BaseException as error:
            outcome = type(error).__name__
            if self.failure:
                try:
                    self.failure(request, stage, started, elapsed_since_last_success, error)
                except Exception as diagnostic_error:
                    print('Healthy issuer diagnostic capture failed: ' +
                          type(diagnostic_error).__name__, file=sys.stderr, flush=True)
            raise
        finally:
            self.stream.settimeout(previous_timeout)
            if self.record:
                self.record('request', started, self.timeout, outcome,
                            elapsed_since_last_success, self.idle_timeout_ms)


HEALTHY_ISSUER_IDLE_TIMEOUT_MS = 15000


class HealthySupervisorIssuer(SupervisorRegistryTests):
    def ready(self):
        # Healthy native fixtures run bounded local setup between issuer calls.
        # Keep this longer lease scoped to this fixture; adversarial TLS tests
        # retain TlsFixture's 3000 ms idle-expiry contract.
        self.config.idle_timeout_ms = HEALTHY_ISSUER_IDLE_TIMEOUT_MS
        return super().ready()

    def record_timing(self, operation, started, timeout, outcome,
                      elapsed_since_last_success=None, idle_timeout_ms=None):
        record = dict(operation=operation, elapsed_seconds=time.monotonic() - started,
                      deadline_seconds=timeout, outcome=outcome, test=self._testMethodName)
        if elapsed_since_last_success is not None:
            record['elapsed_since_last_success_seconds'] = elapsed_since_last_success
            record['idle_timeout_ms'] = idle_timeout_ms
            record['request_start_monotonic'] = started
        print('Healthy issuer timing ' + json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)

    def capture_request_failure(self, request, stage, started, idle_elapsed, error):
        operation_type = request.WhichOneof('operation')
        nested_type = None
        if operation_type == 'challenge':
            nested_type = request.challenge.WhichOneof('action')
        daemons = []
        for index, (proc, log) in enumerate(getattr(self, 'daemons', [])):
            returncode = proc.poll()
            try:
                stat = Path('/proc') / str(proc.pid) / 'stat'
                start_identity = stat.read_text().rsplit(')', 1)[1].split()[19]
            except (OSError, IndexError, ValueError):
                start_identity = None
            try:
                lines = Path(log.name).read_bytes()[-8192:].decode(
                    'utf-8', errors='replace').splitlines()
                markers = []
                for line in lines:
                    delayed = re.fullmatch(
                        r'issuer TLS loop delayed monotonic_us=(-?\d+) poll_ms=(-?\d+) work_ms=(-?\d+)',
                        line)
                    if delayed:
                        markers.append(dict(marker='issuer_tls_loop_delayed',
                                            monotonic_us=int(delayed.group(1)),
                                            poll_ms=int(delayed.group(2)),
                                            work_ms=int(delayed.group(3))))
                markers.append(dict(marker='identityd_error_present',
                                    present=any(line.startswith('identityd: ') for line in lines)))
            except OSError as read_error:
                markers = [dict(marker='stderr_unavailable',
                                error_type=type(read_error).__name__)]
            daemons.append(dict(index=index, pid=proc.pid, start_identity=start_identity,
                                returncode=returncode, stderr_markers=markers))
        record = dict(test=self._testMethodName, stage=stage, operation_type=operation_type,
                      nested_operation_type=nested_type, error_type=type(error).__name__,
                      request_start_monotonic=started,
                      elapsed_since_last_success_seconds=idle_elapsed,
                      idle_timeout_ms=getattr(self.config, 'idle_timeout_ms', None),
                      daemons=daemons)
        target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            directory = Path(target)
            directory.mkdir(parents=True, exist_ok=True)
            output = directory / f'{self._testMethodName}-issuer-failure.json'
            output.write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
        print('Healthy issuer failure ' + json.dumps(record, sort_keys=True),
              file=sys.stderr, flush=True)

    def initialize(self, state=None, public=None):
        started = time.monotonic()
        outcome = 'failed'
        try:
            command = [str(BINARY), '--init', '--state-dir', str(state or self.state),
                       '--owner-public', str(public or self.public_file)]
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                try:
                    stdout, stderr = proc.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    snapshot = dict(pid=proc.pid, operation='initialize_timeout')
                    for name in ('stat', 'wchan', 'syscall', 'cgroup'):
                        try:
                            snapshot[name] = (Path('/proc') / str(proc.pid) / name).read_text()
                        except OSError as error:
                            snapshot[name] = type(error).__name__
                    print('Healthy issuer timeout ' + json.dumps(snapshot, sort_keys=True),
                          file=sys.stderr, flush=True)
                    proc.kill()
                    proc.communicate()
                    raise
                result = subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
            outcome = 'exit_' + str(result.returncode)
            return result
        except BaseException as error:
            outcome = type(error).__name__
            raise
        finally:
            self.record_timing('initialize', started, 15, outcome)

    def connect(self, context=None, hostname='issuer.test', receive_buffer=None):
        started = time.monotonic()
        deadline = started + 5
        raw = socket.socket()
        outcome = 'failed'
        try:
            raw.settimeout(5)
            if receive_buffer:
                raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
            raw.connect(('127.0.0.1', self.port))
            tls_context = context or self.context()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Healthy issuer connection deadline expired')
            raw.settimeout(remaining)
            stream = tls_context.wrap_socket(raw, server_hostname=hostname)
            peer = DeadlinePeer(stream, record=self.record_timing,
                                failure=self.capture_request_failure,
                                idle_timeout_ms=self.config.idle_timeout_ms)
            self.addCleanup(peer.close)
            outcome = 'connected'
            return peer
        except BaseException as error:
            raw.close()
            outcome = type(error).__name__
            raise
        finally:
            self.record_timing('connect', started, 5, outcome)
