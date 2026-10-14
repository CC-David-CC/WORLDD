"""Run issuer migration subprocesses with durable external output receipts."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path


def _evidence_directory(argv):
    source_root = Path(__file__).resolve().parents[2]
    base = Path(os.environ.get("SPATIAL_SECURITY_EVIDENCE") or tempfile.gettempdir()).resolve()
    try:
        base.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise RuntimeError("SPATIAL_SECURITY_EVIDENCE must be outside the source checkout")
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(argv[0]).name)[:48] or "issuer"
    path = base / "issuer-migration-subprocesses" / f"{name}-{os.getpid()}-{uuid.uuid4().hex}"
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    return path


def _write(path, name, data):
    output = path / name
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data or b"")
    return hashlib.sha256(output.read_bytes()).hexdigest()


def run_issuer_migration(argv, *, input=None, capture_output=False, timeout=None, **kwargs):
    """Run one issuer command, preserve byte streams, and reject sanitizer diagnostics."""
    if capture_output is not True:
        raise ValueError("issuer migration subprocess output capture is required")
    argv = [str(value) for value in argv]
    evidence = _evidence_directory(argv)
    try:
        result = subprocess.run(argv, input=input, capture_output=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as error:
        stdout_hash = _write(evidence, "stdout.bin", error.stdout)
        stderr_hash = _write(evidence, "stderr.bin", error.stderr)
        (evidence / "process.json").write_text(json.dumps({
            "argv": argv, "timed_out": True, "timeout_seconds": timeout,
            "stdout_sha256": stdout_hash, "stderr_sha256": stderr_hash,
        }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        raise
    stdout_hash = _write(evidence, "stdout.bin", result.stdout)
    stderr_hash = _write(evidence, "stderr.bin", result.stderr)
    (evidence / "process.json").write_text(json.dumps({
        "argv": argv, "returncode": result.returncode, "timed_out": False,
        "stdout_sha256": stdout_hash, "stderr_sha256": stderr_hash,
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    diagnostics = (result.stdout + result.stderr).lower()
    markers = (b"addresssanitizer", b"leaksanitizer", b"undefinedbehaviorsanitizer", b"runtime error:")
    found = [marker for marker in markers if marker in diagnostics]
    if found:
        raise AssertionError("sanitizer diagnostic in issuer subprocess; evidence=" + str(evidence) +
                             "; markers=" + repr(found))
    return result
