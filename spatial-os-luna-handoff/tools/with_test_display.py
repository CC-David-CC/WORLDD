#!/usr/bin/env python3
"""Run a diagnostic command on a fresh authenticated software X display."""
import os
import socket
import subprocess
import sys


def main():
    if socket.gethostname().split('.')[0] not in ('sb16', 'sb16-2'):
        raise SystemExit('Test displays are restricted to sb16 and sb16-2')
    command = sys.argv[1:]
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        raise SystemExit('Usage: with_test_display.py -- COMMAND [ARGUMENT ...]')
    env = os.environ.copy()
    env['LIBGL_ALWAYS_SOFTWARE'] = '1'
    # xvfb-run creates its own authority cookie and cleans up only its server.
    argv = ['xvfb-run', '--auto-servernum', '--server-num=90',
            '--server-args=-screen 0 1920x1080x24 -nolisten tcp',
            'sh', '-c', 'SPATIAL_TEST_DISPLAY="$DISPLAY" exec "$@"',
            'spatial-test-display', *command]
    return subprocess.run(argv, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
