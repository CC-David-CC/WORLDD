#!/usr/bin/env python3
"""Run exactly one existing geometry unittest without changing its assertions."""
import importlib.util
import sys
import unittest
from pathlib import Path

if len(sys.argv) != 6:
    raise SystemExit("usage: single_case_driver.py TEST_FILE IDENTITYD ISSUER_CLIENT WORLDD METHOD")
test_file = Path(sys.argv[1]).resolve(strict=True)
identityd, issuer_client, worldd = (str(Path(p).resolve(strict=True)) for p in sys.argv[2:5])
method = sys.argv[5]
sys.path.insert(0, str(test_file.parent))
sys.argv = [str(test_file), identityd, issuer_client, worldd]
spec = importlib.util.spec_from_file_location("_geometry_count_limit_diagnostic", test_file)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot import test module: {test_file}")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
if sys.argv != [str(test_file)]:
    raise RuntimeError(f"fixture arguments were not consumed: {sys.argv[1:]}")
case = module.GeometryViewTests(method)
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([case]))
print(f"DIAGNOSTIC_CASE method={method} ran={result.testsRun} failures={len(result.failures)} errors={len(result.errors)} skipped={len(result.skipped)}", flush=True)
if result.testsRun != 1 or result.skipped:
    raise SystemExit(2)
raise SystemExit(0 if result.wasSuccessful() else 1)
