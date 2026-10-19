#!/usr/bin/env python3
"""Start one persistent tmux job on the current, explicitly named host."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
from typing import Callable, Sequence

Runner = Callable[..., subprocess.CompletedProcess]


def start_once(
    *, expected_host: str, session: str, run_dir: Path, command: Sequence[str],
    hostname: str | None = None, runner: Runner = subprocess.run,
) -> dict[str, str]:
    """Create one run directory and one tmux session on this host only."""
    actual_host = (hostname or socket.gethostname()).split('.')[0]
    if actual_host != expected_host:
        raise RuntimeError(f'host mismatch: expected {expected_host}, found {actual_host}')
    if not session or any(ch.isspace() for ch in session):
        raise ValueError('session must be a nonempty tmux-safe name')
    if not command or any(not item for item in command):
        raise ValueError('command must contain nonempty arguments')

    run_dir = Path(run_dir)
    if not run_dir.is_absolute():
        raise ValueError('run_dir must be absolute')
    if run_dir.exists():
        raise FileExistsError(f'refusing existing run directory: {run_dir}')

    existing = runner(['tmux', 'has-session', '-t', session], capture_output=True, text=True)
    if existing.returncode == 0:
        raise FileExistsError(f'refusing existing tmux session: {session}')
    if existing.returncode != 1:
        raise RuntimeError(f'cannot query tmux session {session}: {existing.stderr.strip()}')

    run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    wrapper = run_dir / 'run-worker.sh'
    stdout = run_dir / 'outer.stdout.log'
    stderr = run_dir / 'outer.stderr.log'
    exit_path = run_dir / 'outer.exit'
    script = (
        '#!/bin/bash\nset +e\n'
        + shlex.join(list(command)) + ' >' + shlex.quote(str(stdout))
        + ' 2>' + shlex.quote(str(stderr)) + '\nrc=$?\nprintf "%s\\n" "$rc" > '
        + shlex.quote(str(exit_path)) + '\nexit "$rc"\n'
    )
    wrapper.write_text(script, encoding='utf-8')
    wrapper.chmod(0o700)
    tmux_command = ['tmux', 'new-session', '-d', '-s', session, 'exec bash ' + shlex.quote(str(wrapper))]
    runner(tmux_command, check=True)
    receipt = {
        'host': actual_host,
        'session': session,
        'run_dir': str(run_dir),
        'wrapper': str(wrapper),
        'command': json.dumps(list(command)),
        'tmux_start_returncode': '0',
    }
    (run_dir / 'startup.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == '--':
        command.pop(0)
    receipt = start_once(expected_host=args.expected_host, session=args.session, run_dir=args.run_dir, command=command)
    print(json.dumps(receipt, sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
