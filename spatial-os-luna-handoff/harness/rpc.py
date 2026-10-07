"""Concurrent, bounded protobuf transport to the production core."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
from typing import Any
from .wire import pb, frame, read_message, validate_reply, make_request, result_value, OUTCOMES

class RpcError(RuntimeError):
    def __init__(self, code: str, message: str = "", details: Any = None):
        super().__init__(f"{code}: {message}")
        self.code, self.details = code, details

class TransportError(RuntimeError):
    pass

class Client:
    def __init__(self, argv: list[str], state_dir: Path, evidence_dir: Path,
                 timeout: float = 10.0, extra_env: dict[str, str] | None = None):
        if not argv or not all(isinstance(x, str) and x for x in argv):
            raise ValueError("SUT command must be a nonempty JSON array of arguments")
        self.timeout = timeout
        self.state_dir, self.evidence_dir = Path(state_dir), Path(evidence_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._stderr = (self.evidence_dir / f"stderr-{time.time_ns()}.log").open("wb")
        self._trace = (self.evidence_dir / f"rpc-{time.time_ns()}.jsonl").open("a", encoding="utf-8")
        env = os.environ.copy()
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            argv + ["--state-dir", str(self.state_dir.resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr,
            bufsize=0, start_new_session=True, env=env,
        )
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._trace_lock = threading.Lock()
        self._pending: dict[int, tuple[queue.Queue, str | None]] = {}
        self._counter, self._dead = 0, None
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _record(self, direction: str, data: Any) -> None:
        with self._trace_lock:
            self._trace.write(json.dumps({"direction": direction, "data": data}, allow_nan=False) + "\n")
            self._trace.flush()

    def _fail(self, error: Exception) -> None:
        with self._state_lock:
            self._dead = error
            for slot, _ in self._pending.values():
                slot.put(error)
            self._pending.clear()

    def _read(self) -> None:
        try:
            while True:
                reply = read_message(self.proc.stdout, pb.Reply)
                validate_reply(reply)
                with self._state_lock:
                    pending = self._pending.pop(reply.request_id, None)
                if pending is None:
                    raise TransportError(f"Unsolicited, duplicate, or late response id {reply.request_id}")
                slot, expected = pending
                try:
                    outcome = reply.WhichOneof('outcome')
                    if outcome != 'error' and outcome != expected:
                        raise TransportError('Wrong response type for request')
                    if outcome == 'error':
                        if reply.error.code == pb.ERROR_CODE_UNSPECIFIED:
                            raise TransportError('Unspecified error code')
                        code = pb.ErrorCode.Name(reply.error.code)
                        msg = {'id': reply.request_id, 'error': {'code': code, 'message': reply.error.message}}
                    else:
                        msg = {'id': reply.request_id, 'result': result_value(reply)}
                    self._record("response", msg)
                    slot.put(msg)
                except Exception as exc:
                    # The slot is already removed; wake its waiter before failing the connection.
                    slot.put(TransportError(str(exc)))
                    raise
        except Exception as exc:
            self._fail(exc if isinstance(exc, TransportError) else TransportError(str(exc)))

    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        return self.call_message(make_request(method, {} if params is None else params), timeout=timeout)

    def call_message(self, request: pb.Request, *, timeout: float | None = None) -> Any:
        message = pb.Request()
        message.CopyFrom(request)
        with self._state_lock:
            if self._dead:
                raise self._dead
            self._counter += 1
            ident = self._counter
            message.request_id = ident
            # Correlation can increase the encoded size. Serialize the final
            # message before allocating a waiter, so validation cannot leak it.
            encoded = frame(message)
            slot: queue.Queue = queue.Queue()
            self._pending[ident] = (slot, OUTCOMES.get(message.WhichOneof('operation')))
        method = message.WhichOneof('operation') or 'unknown'
        msg = {'id': ident, 'operation': method}
        try:
            with self._write_lock:
                self._record("request", msg)
                remaining = memoryview(encoded)
                while remaining:
                    count = self.proc.stdin.write(remaining)
                    if not count:
                        raise BrokenPipeError('Protobuf pipe accepted no bytes')
                    remaining = remaining[count:]
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            error = TransportError(str(exc))
            self._fail(error)
            raise error from exc
        try:
            reply = slot.get(timeout=self.timeout if timeout is None else timeout)
        except queue.Empty as exc:
            error = TransportError(f"Deadline exceeded: {method} (id={ident}); no automatic retry")
            self._fail(error)
            raise error from exc
        if isinstance(reply, Exception):
            raise reply
        if "error" in reply:
            e = reply["error"]
            if not isinstance(e, dict) or not isinstance(e.get("code"), str):
                raise TransportError("Malformed error response")
            raise RpcError(e["code"], e.get("message", ""), e.get("details"))
        return reply["result"]

    def stop(self, *, crash: bool = False) -> None:
        # Only signal the process group created by this Client, never a display-wide name.
        try:
            os.killpg(self.proc.pid, signal.SIGKILL if crash else signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait(timeout=3)
        self._reader.join(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout, self._stderr):
            if stream:
                stream.close()
        with self._trace_lock:
            if not self._trace.closed:
                self._trace.close()

    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.stop()
