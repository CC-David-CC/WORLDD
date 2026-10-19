"""Authenticated TLS issuer relay used by native authority fault witnesses."""
import socket
import ssl
import struct
import threading
import issuer_pb2 as identity
from harness.wire import frame

class IssuerRelay:
    """Forward actual issuer requests and replies. Never manufacture authority state."""
    def __init__(self, test, issuer_fixture, after_reply=None):
        self.test = test; self.issuer = issuer_fixture
        self.after_reply = after_reply or (lambda relay, request, reply: True)
        self.stop = threading.Event(); self.errors = []; self.operations = []
        self.connections = 0; self.stream = None; self.closed = False; self.observations = []
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(issuer_fixture.server_cert), str(issuer_fixture.server_key))
        context.load_verify_locations(str(issuer_fixture.service_cert)); context.verify_mode = ssl.CERT_REQUIRED
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
            upstream = self.issuer.connect()
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
                observed_request = identity.IssuerRequest(); observed_request.CopyFrom(request)
                self.operations.append(observed_request.WhichOneof('operation'))
                reply = upstream.request(request); reply.request_id = sequence
                observed_reply = identity.IssuerReply(); observed_reply.CopyFrom(reply)
                self.observations.append((observed_request, observed_reply))
                if self.after_reply(self, request, reply):
                    self.stream.sendall(frame(reply))
        except (OSError, EOFError):
            pass
        except BaseException as error:
            self.errors.append(error)
        finally:
            if self.stream: self.stream.close()
            if upstream: upstream.close()
