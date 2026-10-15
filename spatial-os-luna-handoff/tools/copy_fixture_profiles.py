"""Explicit resource profiles for the copy fixture launchers, never production policy."""
import hashlib
from pathlib import Path
import re
import subprocess

COPY_BINARIES = ('workspaced', 'worldd', 'identityd', 'spatial-issuer-client',
    'spatial-process-scope-probe', 'spatial-storage-owner', 'spatial-app-init',
    'spatial-copy-tree-worker', 'spatial-copy-finalize-worker', 'spatial-security-auth')

def consume_profile(arguments, flag='--profile='):
    arguments = list(arguments)
    choices = [value for value in arguments if value.startswith(flag)]
    if not choices:
        return arguments, 'Debug'
    if len(choices) != 1 or arguments[-1] != choices[0]:
        raise ValueError('The explicit copy profile must occur once, as the final argument')
    profile = choices[0][len(flag):]
    manager_memory_mib(profile)
    return arguments[:-1], profile

def manager_memory_mib(profile):
    if profile not in ('Debug', 'ASan-UBSan'):
        raise ValueError('Unsupported explicit copy resource profile: ' + profile)
    return 512 if profile == 'ASan-UBSan' else 256

def instrumentation_preflight(build, profile):
    manager_memory_mib(profile)
    result = dict(profile=profile, manager_memory_bytes=manager_memory_mib(profile)*2**20,
                  binaries={})
    for name in COPY_BINARIES:
        path = Path(build)/name
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing regular SAN executable: ' + name)
        output = subprocess.run(['readelf', '--dynamic', str(path)], capture_output=True,
                                text=True, timeout=10, check=True).stdout
        needed = re.findall(r'\(NEEDED\).*?\[([^]]+)\]', output)
        sanitizers = [any(item.startswith(library) for item in needed)
                      for library in ('libasan.so.', 'libubsan.so.')]
        if profile == 'ASan-UBSan' and not all(sanitizers):
            raise ValueError('Explicit SAN profile requires ASan and UBSan on every executable: ' + name)
        if profile == 'Debug' and any(sanitizers):
            raise ValueError('Debug profile requires executables without ASan or UBSan: ' + name)
        result['binaries'][name] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                                    needed=needed)
    return result
