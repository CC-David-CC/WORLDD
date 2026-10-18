#!/usr/bin/env python3
"""Install the Bubblewrap entry point and provision broker identity state on world hosts."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import pwd
import stat
import socket
import subprocess

HELPER = Path('/usr/local/libexec/spatial-bwrap')
PROFILE = Path('/etc/apparmor.d/spatial-bwrap')
POLICY = b'abi <abi/4.0>,\ninclude <tunables/global>\nprofile spatial-bwrap /usr/local/libexec/spatial-bwrap flags=(unconfined) {\n  userns,\n}\n'


def provision_registry_directory(path, uid, gid, apply):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not apply:
            return True
        os.mkdir(path, 0o700)
        os.chown(path, uid, gid)
        os.chmod(path, 0o700)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        info = path.lstat()
        created = True
    else:
        created = False
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise SystemExit(f'Broker registry directory must be a real 0700 directory owned by uid {uid}: {path}')
    return created


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] not in ('sb16', 'sb16-2'):
        raise SystemExit('Only sb16 and sb16-2 are authorized')
    operator = pwd.getpwnam('dflanag3')
    registry = Path('/var/lib') / f'spatial-broker-instances-{operator.pw_uid}'
    varlib = Path('/var/lib').lstat()
    if not stat.S_ISDIR(varlib.st_mode) or varlib.st_uid != 0 or varlib.st_mode & 0o022:
        raise SystemExit('Refusing an insecure /var/lib parent for broker identity state')
    registry_missing = not registry.exists() and not registry.is_symlink()
    if not registry_missing:
        provision_registry_directory(registry, operator.pw_uid, operator.pw_gid, False)
    source = Path('/usr/bin/bwrap')
    binary = source.read_bytes()
    expected = [(HELPER, binary, 0o755), (PROFILE, POLICY, 0o644)]
    for target, data, mode in expected:
        if target.is_symlink() or any(p.is_symlink() for p in target.parents):
            raise SystemExit(f'Refusing a symlink in {target}')
        if target.exists() and target.read_bytes() != data:
            raise SystemExit(f'Existing file differs: {target}')
    report = {'host': socket.gethostname(), 'bwrap_sha256': hashlib.sha256(binary).hexdigest(),
              'profile_sha256': hashlib.sha256(POLICY).hexdigest(), 'applied': False,
              'created': [str(target) for target, _, _ in expected if not target.exists()] + ([str(registry)] if registry_missing else [])}
    if args.apply:
        if os.geteuid() != 0:
            raise SystemExit('Apply requires sudo')
        provision_registry_directory(registry, operator.pw_uid, operator.pw_gid, True)
        for target, data, mode in expected:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                with target.open('xb') as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            os.chown(target, 0, 0)
            target.chmod(mode)
        subprocess.run(['/sbin/apparmor_parser', '-r', str(PROFILE)], check=True)
        report['applied'] = True
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
