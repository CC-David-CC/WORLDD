#!/usr/bin/env python3
"""Bound one diagnostic command; preserve unknown child status on timeout."""
import json
import os
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if len(sys.argv) < 6:
    raise SystemExit("usage: bounded_case.py TIMEOUT_SECONDS RECEIPT.json STDOUT.log STDERR.log COMMAND [ARG ...]")
timeout_s = float(sys.argv[1])
receipt_path, stdout_path, stderr_path = map(Path, sys.argv[2:5])
argv = sys.argv[5:]
if not math.isfinite(timeout_s) or timeout_s <= 0 or not argv:
    raise SystemExit("timeout must be positive and command nonempty")
for path in (receipt_path, stdout_path, stderr_path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SystemExit(f"refusing existing output: {path}")
start_utc = datetime.now(timezone.utc).isoformat()
start = time.monotonic()
record = {"argv": argv, "timeout_seconds": timeout_s, "start_utc": start_utc, "test_exit": "UNKNOWN", "terminal": False, "state": "STARTING"}

def persist_receipt():
    temp = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, sort_keys=True, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, receipt_path)

try:
    with stdout_path.open("xb") as out, stderr_path.open("xb") as err:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
        record["pid"] = child.pid
        record["process_group"] = child.pid
        record["state"] = "RUNNING"
        try:
            stat = Path(f"/proc/{child.pid}/stat").read_text(encoding="ascii")
            # The comm field can contain spaces and parentheses. Parse after its final close-paren.
            tail = stat[stat.rfind(")") + 2:].split()
            record["linux_process_start_ticks"] = int(tail[19])
        except (OSError, ValueError, IndexError):
            record["linux_process_start_ticks"] = None
        persist_receipt()
        try:
            rc = child.wait(timeout=timeout_s)
            record["test_exit"] = rc
            record["terminal"] = True
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
            record["state"] = "TIMED_OUT_UNKNOWN"
            record["test_exit"] = "UNKNOWN"
            record["child_may_still_be_running"] = True
except Exception as exc:
    record["launch_error"] = f"{type(exc).__name__}: {exc}"
finally:
    record["end_utc"] = datetime.now(timezone.utc).isoformat()
    record["elapsed_seconds"] = time.monotonic() - start
    if record.get("terminal"):
        record["state"] = "TERMINAL"
    elif record.get("launch_error"):
        record["state"] = "LAUNCH_FAILED"
    persist_receipt()
if record.get("timed_out"):
    raise SystemExit(124)
if not record["terminal"]:
    raise SystemExit(125)
raise SystemExit(int(record["test_exit"]))
