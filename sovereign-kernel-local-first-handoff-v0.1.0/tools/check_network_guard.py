"""Probe socket denial without ever attempting a peer connection."""
from __future__ import annotations
import ctypes
import errno
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path


def probe() -> dict:
    if sys.platform != 'linux':
        raise RuntimeError('Linux native guard required; portable tests cannot substitute')
    status = Path('/proc/self/status').read_text()
    fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
    if fields.get('Seccomp', '').strip() != '2' or fields.get('NoNewPrivs', '').strip() != '1':
        raise RuntimeError('Missing seccomp or no-new-privileges')
    if int(fields.get('CapEff', '1').strip(), 16) != 0:
        raise RuntimeError('Effective Linux capabilities remain')
    checks = []
    for family, name in [(socket.AF_INET,'ipv4'),(socket.AF_INET6,'ipv6'),(socket.AF_UNIX,'unix')]:
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
        except OSError as exc:
            if exc.errno != errno.EPERM:
                raise RuntimeError(f'{name}: wrong denial {exc}') from exc
        else:
            s.close()
            raise RuntimeError(f'{name}: socket creation was allowed')
        checks.append({'test':name+'-socket','status':'PASS'})
    libc = ctypes.CDLL(None, use_errno=True)
    libc.socket.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_int]
    libc.socket.restype=ctypes.c_int
    fd=libc.socket(socket.AF_INET,socket.SOCK_STREAM,0)
    if fd != -1:
        os.close(fd); raise RuntimeError('Native socket bypass')
    if ctypes.get_errno()!=errno.EPERM: raise RuntimeError('Unexpected native denial')
    checks.append({'test':'native-libc-socket','status':'PASS'})
    syscall_numbers={'x86_64':{'socket':41,'io_uring_setup':425}, 'aarch64':{'socket':198,'io_uring_setup':425}}
    if platform.machine() not in syscall_numbers: raise RuntimeError('Unsupported ABI')
    libc.syscall.restype=ctypes.c_long
    numbers=syscall_numbers[platform.machine()]
    for name, num, args in [('raw-socket',numbers['socket'],(socket.AF_INET,socket.SOCK_STREAM,0)),
                          ('io-uring',numbers['io_uring_setup'],(1,0))]:
        rc=libc.syscall(ctypes.c_long(num), *(ctypes.c_long(x) for x in args))
        if rc!=-1 or ctypes.get_errno()!=errno.EPERM: raise RuntimeError(name+' bypass')
        checks.append({'test':name,'status':'PASS'})
    code="""import socket,errno
try:
 s=socket.socket(); s.close(); raise SystemExit(9)
except OSError as e:
 raise SystemExit(0 if e.errno==errno.EPERM else 10)
"""
    subprocess.run([sys.executable,'-I','-c',code],check=True)
    checks.append({'test':'exec-inherits-filter','status':'PASS'})
    socket_fds=[]
    for path in Path('/proc/self/fd').iterdir():
        try:
            if os.readlink(path).startswith('socket:'): socket_fds.append(path.name)
        except FileNotFoundError: pass
    if socket_fds: raise RuntimeError('Inherited socket descriptors: '+repr(socket_fds))
    checks.append({'test':'no-inherited-socket-fds','status':'PASS'})
    return {'profile':'linux-seccomp-v1', 'checks':checks,
            'scope':'socket syscalls and inherited socket descriptors; trusted host and fixture filesystem',
            'namespace_isolation':'not-claimed', 'hostile_agent_confinement':'not-claimed',
            'proof_level':'local enforcement observations, not remote attestation'}
