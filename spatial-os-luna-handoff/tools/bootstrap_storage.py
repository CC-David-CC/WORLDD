#!/usr/bin/env python3
"""Install the reviewed storage-owner binary on an authorized world host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile

target = Path('/usr/local/libexec/spatial-storage-owner')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-binary', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--replace-known-sha256')
    args = parser.parse_args()
    assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
    source = args.owner_binary.resolve(strict=True)
    assert source.is_file() and source.read_bytes()[:4] == b'\x7fELF'
    data = source.read_bytes(); digest = hashlib.sha256(data).hexdigest()
    assert not target.is_symlink() and all(not p.is_symlink() for p in target.parents)
    previous = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
    if previous not in (None, digest):
        assert args.replace_known_sha256 == previous, 'Existing owner differs; supply its recorded hash before replacement'
    report = dict(host=socket.gethostname(), target=str(target), sha256=digest, previous_sha256=previous, applied=False)
    if args.apply:
        assert os.getuid() == 0
        target.parent.mkdir(parents=True, exist_ok=True)
        if previous not in (None, digest):
            backup = target.with_name(target.name+'.previous-'+previous)
            if not backup.exists():
                with backup.open('xb') as out:
                    out.write(target.read_bytes()); out.flush(); os.fsync(out.fileno())
                backup.chmod(0o755)
            assert hashlib.sha256(backup.read_bytes()).hexdigest() == previous
            report['backup'] = str(backup)
        if previous != digest:
            fd, temporary = tempfile.mkstemp(prefix='.spatial-storage-owner-', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as out:
                    out.write(data); out.flush(); os.fsync(out.fileno())
                os.chown(temporary, 0, 0); os.chmod(temporary, 0o755)
                os.replace(temporary, target)
            finally:
                if Path(temporary).exists(): Path(temporary).unlink()
        os.chown(target, 0, 0); target.chmod(0o755)
        directory = os.open(target.parent, os.O_DIRECTORY | os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
        report['applied'] = True
        report['recovery'] = ['sudo', 'cp', '--', report['backup'], str(target)] if 'backup' in report else ['sudo', 'rm', '--', str(target)]
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
