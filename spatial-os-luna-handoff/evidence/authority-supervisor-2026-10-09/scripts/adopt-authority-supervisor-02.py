"""Fast-forward qualified source only. Do not migrate state or restart a runtime."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
repo = r.parent
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
assert len(sys.argv) == 2
expected = sys.argv[1]
assert len(expected) == 40 and all(c in '0123456789abcdef' for c in expected)
def git(*args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
assert git('status', '--porcelain') == '' and git('branch', '--show-current') == 'main'
assert git('remote', 'get-url', 'origin') == 'dflanag3@box-git:/srv/data/git/spacial_desktop.git'
workers = {'qualify-authority-supervisor-02.py', 'finish-authority-supervisor-full-02.py',
    'after-supervisor-full-02.py', 'diagnose-supervisor-broker-handoff-01.py',
    'after-supervisor-broker-baseline-01.py'}
for p in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        if p.parent.stat().st_uid != os.geteuid():
            continue
        args = p.read_bytes().split(b'\0')
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        continue
    if len(args) >= 2 and Path(os.fsdecode(args[0])).name.startswith('python'):
        assert Path(os.fsdecode(args[1])).name not in workers, 'Qualification or baseline is still active'
manifest = json.loads((r / 'local/authority-supervisor-candidate-02.json').read_text())['files']
assert len(manifest) == 281
summary = json.loads((r / ('local/authority-supervisor-full-summary-final-' + host + '-02.json')).read_text())
assert summary['host'] == host and summary['candidate'] == 2 and summary['source_files'] == 281
for profile in ('debug', 'runtime-sanitize'):
    result = summary[profile]
    assert tuple(result[k] for k in ('core_checks', 'core_suites', 'harness_checks', 'P10_cases', 'failed', 'skipped')) == (1383, 91, 51, 151, 0, 0)
    build = r / 'local/m02-authority-supervisor-02' / profile / 'build'
    for n, digest in result['binaries'].items():
        assert hashlib.sha256((build / n).read_bytes()).hexdigest() == digest, n
for n, digest in manifest.items():
    assert hashlib.sha256((r / 'local/m02-authority-supervisor-02/source' / n).read_bytes()).hexdigest() == digest, n
before = git('rev-parse', 'HEAD')
subprocess.run(['git', '-C', str(repo), 'fetch', 'origin', 'main'], check=True)
assert git('rev-parse', 'origin/main') == expected
record = json.loads(git('show', expected + ':spatial-os-luna-handoff/evidence/authority-supervisor-2026-10-09/integration-applied-02.json'))
assert record['integrated'] and record['files'] == manifest
subprocess.run(['git', '-C', str(repo), 'merge', '--ff-only', expected], check=True, stdout=subprocess.DEVNULL)
for n, digest in manifest.items():
    assert hashlib.sha256((r / n).read_bytes()).hexdigest() == digest, n
assert git('rev-parse', 'HEAD') == expected and git('status', '--porcelain') == ''
record = dict(host=host, before=before, commit=expected, source_hashes_matched=len(manifest),
    worktree_clean=True, runtime_restarted=False, operator_state_migrated=False)
(r / ('local/authority-supervisor-adoption-' + host + '-02.json')).write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
