"""Run expanded event coverage with the verified private graphics vendor."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

root = Path('/home/dflanag3/src/spacial_desktop-m04-hooks/spatial-os-luna-handoff')
old = Path('/home/dflanag3/src/spacial_desktop-m04-20261011/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
previous = json.loads((root / 'evidence/m04-configure-request-20261011/glx-sanitize-01/run.json').read_text())
assert previous['status'] == 'passed' and previous['vendor_initialized_and_unloaded']
assert hashlib.sha256(Path(previous['vendor_path']).read_bytes()).hexdigest() == previous['vendor_sha256']
env = {**os.environ, **previous['environment']}
env.pop('SPATIAL_SECURITY_TEST_CORE_ONLY', None)
out = root / 'evidence/m04-event-coverage-20261011'
out.mkdir(parents=True, exist_ok=True)
(out / 'sanitizer-environment.json').write_text(json.dumps({
    'environment': previous['environment'], 'vendor_path': previous['vendor_path'],
    'vendor_sha256': previous['vendor_sha256'],
    'reason': 'Exercise all 47 parent-event checks, including 19 additions, with native ASan/UBSan and GLX.'}, indent=2))
result = subprocess.run(['python3', str(root / 'tools/qualify_atom_increment.py'),
    '--server-build', str(root / 'evidence/m04-configure-request-20261011/build-sanitize-01/run.json'),
    '--runtime-build', str(old / 'local/selection-runtime-sanitize'),
    '--suite', 'parent_metadata', '--evidence', str(out / 'glx-sanitize-01')], env=env, timeout=180)
raise SystemExit(result.returncode)
