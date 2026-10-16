#!/usr/bin/env python3
"""Validate and SIGKILL one supervised fixture broker as the test operator."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import sys
import time


def process_identity(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    start_ticks = stat[stat.rfind(")") + 2:].split()[19]
    executable = os.readlink(f"/proc/{pid}/exe")
    with open(f"/proc/{pid}/exe", "rb") as executable_file:
        digest = hashlib.file_digest(executable_file, "sha256").hexdigest()
    return start_ticks, executable, digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-pid", type=int, required=True)
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--expected-start", required=True)
    parser.add_argument("--expected-executable", required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("operator helper must run as root")
    if args.target_pid != args.expected_pid:
        raise RuntimeError("target PID does not match the supervised broker PID")
    expected = (args.expected_start, args.expected_executable, args.expected_sha256)
    observed = process_identity(args.target_pid)
    if observed != expected:
        raise RuntimeError(f"broker identity mismatch before pidfd: observed={observed!r}")
    descriptor = os.pidfd_open(args.target_pid)
    try:
        if select.select([descriptor], [], [], 0)[0]:
            raise RuntimeError("broker pidfd was already readable before identity recheck")
        after_open = process_identity(args.target_pid)
        if after_open != expected:
            raise RuntimeError(f"broker identity changed after pidfd open: observed={after_open!r}")
        if select.select([descriptor], [], [], 0)[0]:
            raise RuntimeError("broker pidfd became readable during identity recheck")
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        signal_sent = time.monotonic_ns()
        if not select.select([descriptor], [], [], 5)[0]:
            raise TimeoutError("broker pidfd did not confirm death within five seconds")
        death_observed = time.monotonic_ns()
        print(json.dumps(dict(pid=args.target_pid, start_ticks=args.expected_start,
            executable=args.expected_executable, executable_sha256=args.expected_sha256,
            signal_sent_monotonic_ns=signal_sent, death_observed_monotonic_ns=death_observed),
            sort_keys=True))
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"broker kill refused or failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
