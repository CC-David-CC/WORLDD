"""Tamper-evident baseline, not a security boundary against a machine administrator."""
from pathlib import Path
import hashlib
import json

def verify(root: Path, test_ids=None):
    manifest_path=root/"acceptance.lock.json"
    if not manifest_path.is_file():
        return ["acceptance.lock.json missing; baseline cannot be verified"]
    manifest=json.loads(manifest_path.read_text())
    errors=[]
    for name,want in manifest["files"].items():
        path=root/name
        if not path.is_file():errors.append(f"missing locked file: {name}")
        elif hashlib.sha256(path.read_bytes()).hexdigest()!=want:errors.append(f"changed locked file: {name}")
    if test_ids is not None:
        missing=set(manifest["test_ids"])-set(test_ids)
        errors += [f"baseline test not collected: {name}" for name in sorted(missing)]
    return errors
