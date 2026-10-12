#!/usr/bin/env python3
"""Run exactly one issuer-client unittest method without changing its assertions."""
import importlib.util
import sys
import unittest
from pathlib import Path

import os
import tempfile


def require_security_evidence_directory():
    raw = os.environ.get("SPATIAL_SECURITY_EVIDENCE")
    if not raw:
        raise SystemExit("SPATIAL_SECURITY_EVIDENCE is required before importing fixtures")
    path = Path(raw)
    if not path.is_absolute():
        raise SystemExit("SPATIAL_SECURITY_EVIDENCE must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SystemExit(f"SPATIAL_SECURITY_EVIDENCE must exist: {exc}")
    if not resolved.is_dir():
        raise SystemExit("SPATIAL_SECURITY_EVIDENCE must be a directory")
    probe = None
    try:
        fd, name = tempfile.mkstemp(prefix=".spatial-evidence-writecheck-", dir=resolved)
        os.close(fd)
        probe = Path(name)
    except OSError as exc:
        raise SystemExit(f"SPATIAL_SECURITY_EVIDENCE is not writable: {exc}")
    finally:
        if probe is not None:
            try:
                probe.unlink()
            except OSError as exc:
                raise SystemExit(f"cannot remove evidence write check: {exc}")
    os.environ["SPATIAL_SECURITY_EVIDENCE"] = str(resolved)


require_security_evidence_directory()


if len(sys.argv) != 5:
    raise SystemExit("usage: issuer_case_driver.py TEST_FILE IDENTITYD ISSUER_CLIENT METHOD")
test_file = Path(sys.argv[1]).resolve(strict=True)
identityd, issuer_client = (str(Path(p).resolve(strict=True)) for p in sys.argv[2:4])
method = sys.argv[4]
sys.path.insert(0, str(test_file.parent))
# Import dependencies pop the same two binary arguments used by the frozen CTest entry.
sys.argv = [str(test_file), identityd, issuer_client]
spec = importlib.util.spec_from_file_location("_issuer_callback_diagnostic", test_file)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot import test module: {test_file}")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
if sys.argv != [str(test_file)]:
    raise RuntimeError(f"fixture arguments were not consumed: {sys.argv[1:]}")
if not hasattr(module, "ClientTests"):
    raise RuntimeError("expected ClientTests fixture class is missing")
case = module.ClientTests(method)
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([case]))
print(f"DIAGNOSTIC_CASE method={method} ran={result.testsRun} failures={len(result.failures)} errors={len(result.errors)} skipped={len(result.skipped)}", flush=True)
if result.testsRun != 1 or result.skipped:
    raise SystemExit(2)
raise SystemExit(0 if result.wasSuccessful() else 1)
