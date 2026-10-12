"""Bounded healthy supervisor setup; fault fixtures retain their own deadlines."""
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

import issuer_pb2 as pb
from harness.wire import frame
from test_identity_issuer import BINARY
from test_identity_tls import Peer
from test_supervisor_registry import SupervisorRegistryTests


class DeadlinePeer(Peer):
    def __init__(self, stream, timeout=5, record=None):
        super().__init__(stream)
        self.timeout = timeout
        self.record = record

    def request(self, request):
        started = time.monotonic()
        deadline = started + self.timeout
        diagnostic_owner = getattr(self.record, "__self__", None)
        print("Issuer diagnostic " + json.dumps(dict(operation=request.WhichOneof("operation"), started=started, idle_gap_seconds=(started - self.last_finished) if hasattr(self, "last_finished") else None, issuer_processes=[dict(pid=proc.pid, exit=proc.poll()) for proc, _ in getattr(diagnostic_owner, "daemons", [])])), file=sys.stderr, flush=True)
        previous_timeout = self.stream.gettimeout()
        outcome = 'failed'

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
            length = struct.unpack('!I', exact(4))[0]
            if length > 1048576:
                raise AssertionError('Excessive issuer reply')
            reply = pb.IssuerReply.FromString(exact(length))
            if reply.protocol_version != 1 or reply.request_id != self.sequence:
                raise AssertionError('Wrong issuer reply identity')
            outcome = 'received'
            return reply
        except BaseException as error:
            outcome = type(error).__name__
            raise
        finally:
            self.last_finished = time.monotonic()
            self.stream.settimeout(previous_timeout)
            if self.record:
                self.record('request', started, self.timeout, outcome)


class HealthySupervisorIssuer(SupervisorRegistryTests):
    def record_timing(self, operation, started, timeout, outcome):
        record = dict(operation=operation, elapsed_seconds=time.monotonic() - started,
                      deadline_seconds=timeout, outcome=outcome, test=self._testMethodName)
        print('Healthy issuer timing ' + json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)

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
            peer = DeadlinePeer(stream, record=self.record_timing)
            self.addCleanup(peer.close)
            outcome = 'connected'
            return peer
        except BaseException as error:
            raw.close()
            outcome = type(error).__name__
            raise
        finally:
            self.record_timing('connect', started, 5, outcome)
