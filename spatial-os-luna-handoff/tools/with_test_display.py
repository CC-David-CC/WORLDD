#!/usr/bin/env python3
"""Run a diagnostic command on a fresh authenticated software X display."""
import os
from pathlib import Path
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
    if server := env.get('SPATIAL_TEST_XSERVER'):
        binary = Path(server)
        if not binary.is_absolute() or binary.name != 'Xvfb' or not os.access(binary, os.X_OK):
            raise SystemExit('SPATIAL_TEST_XSERVER must name an executable absolute Xvfb path')
        # xvfb-run starts Xvfb through PATH and retains its existing Xauthority handling.
        env['PATH'] = str(binary.parent) + os.pathsep + env.get('PATH', '')
    # xvfb-run creates its own authority cookie and cleans up only its server.
    argv = ['xvfb-run', '--auto-servernum', '--server-num=90',
            '--server-args=-screen 0 1920x1080x24 -nolisten tcp',
            'sh', '-c', 'SPATIAL_TEST_DISPLAY="$DISPLAY" exec "$@"',
            'spatial-test-display', *command]
    if log := env.get('SPATIAL_TEST_XSERVER_LOG'):
        path = Path(log)
        if not path.is_absolute():
            raise SystemExit('SPATIAL_TEST_XSERVER_LOG must be absolute')
        argv.insert(1, '--error-file=' + str(path))
    return subprocess.run(argv, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
