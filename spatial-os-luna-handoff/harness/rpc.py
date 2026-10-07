"""Strict, concurrent JSON-lines transport to the real system-under-test adapter."""
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
        self._pending: dict[int, queue.Queue] = {}
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
            for slot in self._pending.values():
                slot.put(error)
            self._pending.clear()

    def _read(self) -> None:
        try:
            while True:
                raw = self.proc.stdout.readline(8 * 1024 * 1024 + 1)
                if not raw:
                    raise TransportError("SUT closed stdout; inspect retained stderr")
                if not raw.endswith(b"\n") or len(raw) > 8 * 1024 * 1024:
                    raise TransportError("Oversized or unterminated JSON response")
                msg = json.loads(raw, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
                if not isinstance(msg, dict) or type(msg.get("id")) is not int:
                    raise TransportError("Response must have an integer id")
                if ("result" in msg) == ("error" in msg):
                    raise TransportError("Response needs exactly one of result/error")
                self._record("response", msg)
                with self._state_lock:
                    slot = self._pending.pop(msg["id"], None)
                if slot is None:
                    raise TransportError(f"Unsolicited, duplicate, or late response id {msg['id']}")
                slot.put(msg)
        except Exception as exc:
            self._fail(exc if isinstance(exc, TransportError) else TransportError(str(exc)))

    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        with self._state_lock:
            if self._dead:
                raise self._dead
            self._counter += 1
            ident = self._counter
            slot: queue.Queue = queue.Queue()
            self._pending[ident] = slot
        msg = {"id": ident, "method": method, "params": params or {}}
        try:
            encoded = json.dumps(msg, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        except Exception:
            with self._state_lock:
                self._pending.pop(ident, None)
            raise
        try:
            with self._write_lock:
                self._record("request", msg)
                self.proc.stdin.write(encoded)
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise TransportError(str(exc)) from exc
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
