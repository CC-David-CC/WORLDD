"""Run selected checks in a bounded transient system service on an authorized host."""
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import uuid

assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
native = sys.argv[1:2] == ['--native']
if native: sys.argv.pop(1)
copy_engine_v2 = sys.argv[1:2] == ['--copy-engine-v2']
if copy_engine_v2:
    assert native, 'The copy engine profile requires the native resource boundary'
    sys.argv.pop(1)
assert sys.argv[1:2] == ['--'] and len(sys.argv) > 2
assert os.getuid() != 0, 'Invoke as the project owner; only systemd setup uses sudo'
unit = 'spatial-m03-resources-' + uuid.uuid4().hex + '.service'
command = ['sudo', '-n', 'systemd-run', '--unit='+unit, '--wait', '--pipe', '--collect',
           '--service-type=exec', '--uid='+pwd.getpwuid(os.getuid()).pw_name,
           '--working-directory='+str(Path.cwd()), '-p', 'Delegate=cpu memory pids io',
           '-p', 'DelegateSubgroup=manager', '-p', 'MemoryMax='+('1536M' if native else '512M'), '-p', 'MemorySwapMax=0',
           '-p', 'TasksMax='+('256' if native else '128'), '-p', 'CPUQuota='+('400%' if native else '200%'),
           '-p', 'RuntimeMaxSec='+('900' if copy_engine_v2 else '240' if native else '120'),
           '-p', 'TimeoutStopSec=5s']
for name in ('PYTHONPATH', 'ASAN_OPTIONS', 'LSAN_OPTIONS', 'UBSAN_OPTIONS', 'SPATIAL_SCOPE_EVIDENCE',
             'SPATIAL_TEST_XSERVER', 'SPATIAL_SECURITY_EVIDENCE', 'SPATIAL_XPROBE', 'SPATIAL_RENDER_EVIDENCE'):
    if name in os.environ:
        command.append('--setenv='+name+'='+os.environ[name])
command += ['--', *sys.argv[2:]]
print(json.dumps(dict(unit=unit, command=command, recovery=['sudo','-n','systemctl','stop',unit])), flush=True)
result = subprocess.run(command)
# A failed transient unit may remain registered briefly. Stop only our exact unit.
if result.returncode:
    cleanup = subprocess.run(['sudo','-n','systemctl','stop',unit], capture_output=True, text=True)
    print(json.dumps(dict(cleanup_exit=cleanup.returncode, cleanup_stderr=cleanup.stderr)), flush=True)
state = subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','LoadState'], capture_output=True, text=True)
print(json.dumps(dict(unit_after=state.stdout, query_exit=state.returncode)), flush=True)
raise SystemExit(result.returncode)
