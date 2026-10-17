#!/usr/bin/env python3
"""Run one test command with a fresh, owner-only diagnostic directory."""
import argparse
import os
from pathlib import Path
import stat
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        parser.error('a test command is required after --')

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise RuntimeError('private evidence root permissions are not owner-only')
    evidence = Path(tempfile.mkdtemp(prefix='actor-cabinet-', dir=root))
    os.chmod(evidence, 0o700)
    env = os.environ.copy()
    env['SPATIAL_SECURITY_EVIDENCE'] = str(evidence)
    return subprocess.run(command, env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
