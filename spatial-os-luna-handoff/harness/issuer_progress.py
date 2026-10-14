"""Real issuer lifetime observations and a bounded authenticated INFO-delay relay."""
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import threading
import time
import issuer_pb2 as issuer
from google.protobuf import text_format
from harness.wire import frame
from test_world_authority import RelayingIssuer


class IssuerLifetimeWitness:
    """Record safe policy and owned process/cgroup facts without key material."""
    def capture_issuer_lifetime(self, stage):
        target = Path(os.environ["SPATIAL_SECURITY_EVIDENCE"])
        target.mkdir(parents=True, exist_ok=True)
        observations = getattr(self, "_issuer_observations", [])
        self._issuer_observations = observations
        daemons = getattr(self, "daemons", [])
        self.assertLessEqual(len(daemons), 8)
        processes = []
        policies = getattr(self, "_issuer_server_policies", {})
        for proc, _ in daemons:
            exit_code = proc.poll()
            pinned = policies.get(proc.pid)
            item = dict(pid=proc.pid, exit_code=exit_code, start_ticks=None, cgroup=None, memory_events=[],
                server_policy=dict(zip(("protocol_version", "io_timeout_ms", "idle_timeout_ms", "connection_limit"), pinned[0])) if pinned else None,
                server_config_sha256=pinned[1] if pinned else None)
            stat_path = Path("/proc") / str(proc.pid) / "stat"
            starts = getattr(self, "_issuer_start_ticks", {})
            self._issuer_start_ticks = starts
            try:
                raw = stat_path.read_text() if exit_code is None else None
            except FileNotFoundError:
                raw = None
                item["exit_code"] = proc.poll()
                item["disappeared_during_sample"] = True
            if raw is not None:
                item["start_ticks"] = raw[raw.rfind(")")+2:].split()[19]
                expected = starts.setdefault(proc.pid, item["start_ticks"])
                item["identity_matches"] = item["start_ticks"] == expected
                self.assertTrue(item["identity_matches"], "Owned issuer process identity changed")
                try:
                    relative = (stat_path.parent/"cgroup").read_text().strip().split("::", 1)[1]
                except FileNotFoundError:
                    item["exit_code"] = proc.poll()
                    item["disappeared_during_sample"] = True
                    processes.append(item)
                    continue
                group = Path("/sys/fs/cgroup") / relative.lstrip("/")
                assert group.is_relative_to(Path("/sys/fs/cgroup")) and ".." not in group.parts
                item["cgroup"] = str(group)
                # Only the owned process group and its scoped service ancestors.
                for depth in range(8):
                    path = group/"memory.events"
                    if path.exists():
                        raw = path.read_text()
                        assert len(raw) <= 8192
                        item["memory_events"].append(dict(path=str(path), values={
                            key:int(value) for key,value in (line.split() for line in raw.splitlines())}))
                    if group.name.endswith(".service") or depth == 0 and not any(
                            part.startswith("spatial-m03-resources-") for part in group.parts):
                        break
                    group = group.parent
            processes.append(item)
        observations.append(dict(stage=stage, monotonic_ns=time.monotonic_ns(),
            policy=processes[-1]["server_policy"] if processes else None,
            processes=processes))
        self.assertLessEqual(len(observations), 32)
        path = target/(self._testMethodName+"-issuer-lifetime.json")
        path.write_text(json.dumps(observations, sort_keys=True, indent=2)+"\n")
        path.chmod(0o600)
        return observations[-1]

    def launch(self, *args, **kwargs):
        proc = super().launch(*args, **kwargs)
        # Pin the exact serialized configuration passed to this real issuer.
        # Other fixtures later reuse self.config for their broker configuration.
        arguments = list(proc.args)
        self.assertEqual(arguments.count("--tls-config"), 1)
        payload = Path(arguments[arguments.index("--tls-config")+1]).read_bytes()
        configured = text_format.Parse(payload.decode(), issuer.IssuerTlsServerConfig())
        policies = getattr(self, "_issuer_server_policies", {})
        self._issuer_server_policies = policies
        self.assertLess(len(policies), 8)
        policies[proc.pid] = ((configured.protocol_version, configured.io_timeout_ms,
            configured.idle_timeout_ms, configured.connection_limit), hashlib.sha256(payload).hexdigest())
        self.capture_issuer_lifetime("after_actual_tls_launch")
        return proc

    def stop_daemons(self):
        try:
            self.capture_issuer_lifetime("before_actual_tls_shutdown")
        finally:
            # Evidence failure must never skip actual owned-daemon shutdown.
            # Neither finally block returns or suppresses the observation error.
            try:
                super().stop_daemons()
            finally:
                self.capture_issuer_lifetime("after_actual_tls_shutdown")


class DelayedInfoIssuer(RelayingIssuer):
    """Forward real issuer replies, holding one selected INFO for exactly4.1s."""
    def __init__(self, fixture):
        self.delay_armed = threading.Event()
        self.delay_entered = threading.Event()
        self.delay_finished = threading.Event()
        self.delay_records = []
        super().__init__(fixture, lambda: None)

    def relay(self, raw):
        upstream = None
        stream = raw
        try:
            raw.settimeout(2)
            stream = self.context.wrap_socket(raw, server_side=True)
            self.streams.append(stream)
            upstream = self.fixture.connect()
            def exact(size):
                data = b""
                while len(data) < size:
                    part = stream.recv(size-len(data))
                    if not part: raise EOFError()
                    data += part
                return data
            while not self.stop.is_set():
                size = struct.unpack("!I", exact(4))[0]
                if not 0 < size <= 1048576: raise AssertionError("Excessive INFO relay frame")
                request = issuer.IssuerRequest.FromString(exact(size))
                sequence = request.request_id
                reply = upstream.request(request)
                reply.request_id = sequence
                delay = False
                if request.HasField("info"):
                    with self.status_lock:
                        if self.delay_armed.is_set():
                            self.delay_armed.clear()
                            delay = True
                if delay:
                    started = time.monotonic()
                    record = dict(operation="info", start_monotonic=started,
                                  reply_sha256=hashlib.sha256(reply.SerializeToString()).hexdigest())
                    self.delay_records.append(record)
                    self.delay_entered.set()
                    next_upstream_keepalive = started+.5
                    while time.monotonic()-started < 4.1:
                        if self.stop.wait(.01): raise EOFError()
                        if time.monotonic() >= next_upstream_keepalive:
                            # Keep the relay-to-real-issuer leg healthy while the
                            # world waits for its original, unchanged INFO reply.
                            alive = upstream.call(info=issuer.IssuerInfoRequest())
                            if not alive.HasField("info"): raise AssertionError(alive)
                            next_upstream_keepalive = time.monotonic()+.5
                    record["end_monotonic"] = time.monotonic()
                    record["elapsed_seconds"] = record["end_monotonic"]-started
                    send_started = time.monotonic()
                    stream.sendall(frame(reply))
                    record["forward_elapsed_seconds"] = time.monotonic()-send_started
                    self.delay_finished.set()
                else:
                    stream.sendall(frame(reply))
        except (OSError, EOFError):
            pass
        except BaseException as error:
            self.errors.append(error)
        finally:
            stream.close()
            if upstream: upstream.close()
