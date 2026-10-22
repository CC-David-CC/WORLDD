#!/usr/bin/env python3
"""Run selected checks in a bounded transient system service on an authorized host."""
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import uuid

ENVIRONMENT_ALLOWLIST = (
    'PYTHONPATH', 'ASAN_OPTIONS', 'LSAN_OPTIONS', 'UBSAN_OPTIONS',
    'PYTHONDONTWRITEBYTECODE',
    'SPATIAL_SCOPE_EVIDENCE', 'SPATIAL_SCOPE_READTRACE_PATH', 'SPATIAL_TEST_XSERVER',
    'SPATIAL_TEST_XSERVER_SHA256', 'SPATIAL_TEST_DEFAULT_XSERVER',
    'SPATIAL_TEST_OBSERVER_XSERVER', 'SPATIAL_TEST_OBSERVER_XSERVER_SHA256',
    'SPATIAL_SECURITY_EVIDENCE', 'SPATIAL_XPROBE', 'SPATIAL_RENDER_EVIDENCE',
    'SPATIAL_EVIDENCE_DIR',
    'SPATIAL_TEST_STORAGE_VOLUME', 'SPATIAL_TEST_STORAGE_LAUNCH_CONFIG',
    'SPATIAL_TEST_STORAGE_COPY_QUOTA_BYTES',
)


def build_service_command(*, unit, username, native, copy_engine_v2,
                          working_directory, environment, argv, runtime_seconds=None):
    if runtime_seconds is not None:
        if type(runtime_seconds) is not int or not 1 <= runtime_seconds <= 900:
            raise ValueError('runtime_seconds must be an integer from 1 through 900')
        if native is not True or copy_engine_v2 is not False:
            raise ValueError('runtime_seconds is allowed only for the native non-copy profile')
    runtime_limit = (runtime_seconds if runtime_seconds is not None else
                     900 if copy_engine_v2 else 240 if native else 120)
    command = ['sudo', '-n', 'systemd-run', '--unit='+unit, '--wait', '--pipe', '--collect',
               '--service-type=exec', '--uid='+username,
               '--working-directory='+str(working_directory), '-p', 'Delegate=cpu memory pids io',
               '-p', 'DelegateSubgroup=manager', '-p', 'MemoryMax='+('1536M' if native else '512M'), '-p', 'MemorySwapMax=0',
               '-p', 'TasksMax='+('256' if native else '128'), '-p', 'CPUQuota='+('400%' if native else '200%'),
               '-p', 'RuntimeMaxSec='+str(runtime_limit),
               '-p', 'TimeoutStopSec=5s']
    for name in ENVIRONMENT_ALLOWLIST:
        if name in environment:
            command.append('--setenv='+name+'='+environment[name])
    return command + ['--', *argv]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if socket.gethostname().split('.')[0] not in ('sb16', 'sb16-2'):
        raise SystemExit('Resource delegation is restricted to sb16 and sb16-2')
    native = False
    copy_engine_v2 = False
    runtime_seconds = None
    while args and args[0] != '--':
        option = args.pop(0)
        if option == '--native' and not native:
            native = True
        elif option == '--copy-engine-v2' and not copy_engine_v2:
            copy_engine_v2 = True
        elif option == '--runtime-seconds' and runtime_seconds is None:
            if not args or args[0] == '--':
                raise SystemExit('--runtime-seconds requires an integer from 1 through 900')
            value = args.pop(0)
            if not value.isascii() or not value.isdecimal():
                raise SystemExit('--runtime-seconds requires an integer from 1 through 900')
            try:
                runtime_seconds = int(value)
            except ValueError:
                raise SystemExit('--runtime-seconds requires an integer from 1 through 900') from None
            if not 1 <= runtime_seconds <= 900:
                raise SystemExit('--runtime-seconds requires an integer from 1 through 900')
        else:
            raise SystemExit(f'Invalid or duplicate option: {option}')
    if copy_engine_v2 and not native:
        raise SystemExit('The copy engine profile requires the native resource boundary')
    if runtime_seconds is not None and (not native or copy_engine_v2):
        raise SystemExit('--runtime-seconds is allowed only for the native non-copy profile')
    if args[:1] != ['--'] or len(args) <= 1:
        raise SystemExit('Usage: with_resource_delegate.py [--native] [--copy-engine-v2] [--runtime-seconds N] -- COMMAND [ARGUMENT ...]')
    if os.getuid() == 0:
        raise SystemExit('Invoke as the project owner; only systemd setup uses sudo')
    unit = 'spatial-m03-resources-' + uuid.uuid4().hex + '.service'
    command = build_service_command(
        unit=unit, username=pwd.getpwuid(os.getuid()).pw_name, native=native,
        copy_engine_v2=copy_engine_v2, working_directory=Path.cwd(),
        environment=os.environ, argv=args[1:], runtime_seconds=runtime_seconds)
    print(json.dumps(dict(unit=unit, command=command, recovery=['sudo','-n','systemctl','stop',unit])), flush=True)
    result = subprocess.run(command)
    # A failed transient unit may remain registered briefly. Stop only our exact unit.
    if result.returncode:
        cleanup = subprocess.run(['sudo','-n','systemctl','stop',unit], capture_output=True, text=True)
        print(json.dumps(dict(cleanup_exit=cleanup.returncode, cleanup_stderr=cleanup.stderr)), flush=True)
    state = subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','LoadState'], capture_output=True, text=True)
    print(json.dumps(dict(unit_after=state.stdout, query_exit=state.returncode)), flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
