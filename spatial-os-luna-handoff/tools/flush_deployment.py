#!/usr/bin/env python3
"""Flush deployment writeback before starting runtime qualification."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time


def memory_writeback():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith(('Dirty:', 'Writeback:')):
            name, value, unit = line.split()
            if unit != 'kB':
                raise RuntimeError('Unknown kernel writeback unit')
            values[name.rstrip(':') + '_kib'] = int(value)
    return values


def flush_payload(payload, timeout=120):
    """Return a writeback receipt. This does not verify or qualify the payload."""
    payload = Path(payload)
    if not payload.is_absolute() or payload.resolve(strict=True) != payload:
        raise ValueError('Deployment directory must be absolute and canonical')
    if not 0 < timeout <= 600:
        raise ValueError('Invalid deployment flush timeout')
    fd = os.open(payload, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid():
            raise ValueError('Deployment directory must be owned by the caller')
        report = {
            'status': 'PENDING', 'payload': str(payload), 'device': before.st_dev,
            'inode': before.st_ino, 'started_unix': time.time(), 'timeout_seconds': timeout,
            'tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'before': memory_writeback(),
        }
        started = time.monotonic()
        try:
            # The child opens this inherited pinned directory. Renames cannot
            # redirect syncfs to an unrelated path or filesystem.
            result = subprocess.run(['sync', '-f', '--', f'/proc/self/fd/{fd}'],
                                    pass_fds=(fd,), capture_output=True, text=True, timeout=timeout)
            report['exit'] = result.returncode
            report['stderr'] = result.stderr[:4096]
            current = payload.stat(follow_symlinks=False)
            if result.returncode == 0 and (current.st_dev, current.st_ino) == (before.st_dev, before.st_ino):
                report['status'] = 'WRITEBACK_FLUSHED'
            else:
                report['status'] = 'FAILED'
        except subprocess.TimeoutExpired:
            report['status'] = 'TIMEOUT'
        except OSError as error:
            report['status'] = 'FAILED'
            report['error'] = type(error).__name__
        report['seconds'] = time.monotonic() - started
        report['after'] = memory_writeback()
        return report
    finally:
        os.close(fd)


def write_receipt(path, report):
    path = Path(path)
    payload = Path(report['payload'])
    if not path.is_absolute() or path.parent.resolve(strict=True) != path.parent or path == payload or payload in path.parents:
        raise ValueError('Writeback receipt must be outside the immutable payload')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(report, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    report = flush_payload(args.payload, args.timeout)
    write_receipt(args.receipt, report)
    print(json.dumps(report, indent=2), flush=True)
    if report['status'] != 'WRITEBACK_FLUSHED':
        return 1
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if command:
        os.execvp(command[0], command)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
