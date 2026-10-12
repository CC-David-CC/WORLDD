#!/usr/bin/env python3
"""Install the project-specific, unprivileged Bubblewrap entry point on allowed hosts."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import socket
import subprocess

HELPER = Path('/usr/local/libexec/spatial-bwrap')
PROFILE = Path('/etc/apparmor.d/spatial-bwrap')
POLICY = b'abi <abi/4.0>,\ninclude <tunables/global>\nprofile spatial-bwrap /usr/local/libexec/spatial-bwrap flags=(unconfined) {\n  userns,\n}\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] not in ('sb16', 'sb16-2'):
        raise SystemExit('Only sb16 and sb16-2 are authorized')
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
              'created': [str(target) for target, _, _ in expected if not target.exists()]}
    if args.apply:
        if os.geteuid() != 0:
            raise SystemExit('Apply requires sudo')
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
