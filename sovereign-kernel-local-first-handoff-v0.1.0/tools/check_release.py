#!/usr/bin/env python3
"""Verify the distributed files listed in SHA256SUMS without networking."""
from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parents[1]
count=0
for line in (ROOT/'SHA256SUMS').read_text().splitlines():
    digest,name=line.split('  ',1)
    path=ROOT/name
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
        raise SystemExit('Unsafe release path: '+name)
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=digest: raise SystemExit('Checksum mismatch: '+name)
    count+=1
print(f'Verified {count} listed release files. This establishes byte consistency, not authority or safety.')
