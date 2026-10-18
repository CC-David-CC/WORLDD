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
    'SPATIAL_SECURITY_EVIDENCE', 'SPATIAL_XPROBE', 'SPATIAL_RENDER_EVIDENCE',
    'SPATIAL_EVIDENCE_DIR',
)


def build_service_command(*, unit, username, native, copy_engine_v2,
                          working_directory, environment, argv):
    command = ['sudo', '-n', 'systemd-run', '--unit='+unit, '--wait', '--pipe', '--collect',
               '--service-type=exec', '--uid='+username,
               '--working-directory='+str(working_directory), '-p', 'Delegate=cpu memory pids io',
               '-p', 'DelegateSubgroup=manager', '-p', 'MemoryMax='+('1536M' if native else '512M'), '-p', 'MemorySwapMax=0',
               '-p', 'TasksMax='+('256' if native else '128'), '-p', 'CPUQuota='+('400%' if native else '200%'),
               '-p', 'RuntimeMaxSec='+('900' if copy_engine_v2 else '240' if native else '120'),
               '-p', 'TimeoutStopSec=5s']
    for name in ENVIRONMENT_ALLOWLIST:
        if name in environment:
            command.append('--setenv='+name+'='+environment[name])
    return command + ['--', *argv]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if socket.gethostname().split('.')[0] not in ('sb16', 'sb16-2'):
        raise SystemExit('Resource delegation is restricted to sb16 and sb16-2')
    native = args[:1] == ['--native']
    if native:
        args.pop(0)
    copy_engine_v2 = args[:1] == ['--copy-engine-v2']
    if copy_engine_v2:
        if not native:
            raise SystemExit('The copy engine profile requires the native resource boundary')
        args.pop(0)
    if args[:1] != ['--'] or len(args) <= 1:
        raise SystemExit('Usage: with_resource_delegate.py [--native] [--copy-engine-v2] -- COMMAND [ARGUMENT ...]')
    if os.getuid() == 0:
        raise SystemExit('Invoke as the project owner; only systemd setup uses sudo')
    unit = 'spatial-m03-resources-' + uuid.uuid4().hex + '.service'
    command = build_service_command(
        unit=unit, username=pwd.getpwuid(os.getuid()).pw_name, native=native,
        copy_engine_v2=copy_engine_v2, working_directory=Path.cwd(),
        environment=os.environ, argv=args[1:])
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
