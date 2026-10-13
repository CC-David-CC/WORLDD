#!/usr/bin/env python3
"""Own one exact delegated systemd unit for the binary geometry witness fixture."""
import json
import os
from pathlib import Path
import pwd
import re
import socket
import subprocess
import sys

if socket.gethostname().split('.')[0] not in ('sb16','sb16-2'):
    raise SystemExit('Geometry delegation runs only on qualification hosts')
if len(sys.argv)<4 or sys.argv[2]!='--' or not re.fullmatch(
        r'spatial-m03-resources-m06-geometry-[0-9a-f]{32}\.service',sys.argv[1]):
    raise SystemExit('Expected exact geometry unit and command argv')
if os.getuid()==0:raise SystemExit('Run as the project owner')
unit=sys.argv[1]
command=['sudo','-n','systemd-run','--unit='+unit,'--wait','--pipe','--collect',
    '--service-type=exec','--uid='+pwd.getpwuid(os.getuid()).pw_name,
    '--working-directory='+str(Path.cwd()),'-p','Delegate=cpu memory pids io',
    '-p','DelegateSubgroup=manager','-p','MemoryMax=1536M','-p','MemorySwapMax=0',
    '-p','TasksMax=256','-p','CPUQuota=400%','-p','RuntimeMaxSec=240','-p','TimeoutStopSec=5s']
for name in ('PYTHONPATH','ASAN_OPTIONS','LSAN_OPTIONS','UBSAN_OPTIONS','SPATIAL_SCOPE_EVIDENCE',
             'SPATIAL_TEST_XSERVER','SPATIAL_SECURITY_EVIDENCE','SPATIAL_XPROBE','SPATIAL_RENDER_EVIDENCE'):
    if name in os.environ:command.append('--setenv='+name+'='+os.environ[name])
command+=['--',*sys.argv[3:]]
print(json.dumps(dict(unit=unit,command=command,recovery=['sudo','-n','systemctl','stop',unit])),flush=True)
result=subprocess.run(command)
if result.returncode:
    recovered=subprocess.run(['sudo','-n','systemctl','stop',unit],capture_output=True,text=True,timeout=10)
    print(json.dumps(dict(recovery_exit=recovered.returncode,recovery_stderr=recovered.stderr)),flush=True)
state=subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','LoadState'],capture_output=True,text=True,timeout=10)
print(json.dumps(dict(unit=unit,unit_after=state.stdout,query_exit=state.returncode,
    cgroup_exists=(Path('/sys/fs/cgroup/system.slice')/unit).exists())),flush=True)
raise SystemExit(result.returncode)
