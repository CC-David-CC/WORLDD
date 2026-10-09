"""Integrate reviewed origin/main only after local qualification and source validation."""
import hashlib
import json
import pathlib
import re
import socket
import subprocess
import sys

root = pathlib.Path('/home/dflanag3/src/spacial_desktop')
project = root / 'spatial-os-luna-handoff'
base = '71386ba45d9f55368e5b1bb53913bbf30bcdce27'
expected = sys.argv[1]
assert re.fullmatch('[0-9a-f]{40}', expected)

def git(*arguments):
    return subprocess.check_output(['git', *arguments], cwd=root, text=True).rstrip('\n')

assert socket.gethostname() in {'sb16', 'sb16-2'}
assert git('rev-parse', 'HEAD') == base
assert git('branch', '--show-current') == 'main'
summary = json.loads((project / 'local/actor-pixels-evidence-export/summary.json').read_text())
for profile in ['debug', 'runtime-sanitize']:
    assert summary[profile] == dict(core_suites=47, core_checks=914, harness_checks=51, acceptance_checks=151, failed=0, skipped=0)
inputs = json.loads((project / 'local/actor-pixels-tested-inputs.json').read_text())
for path, digest in inputs.items():
    assert hashlib.sha256((root/path).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest, path
status = git('status', '--porcelain=v1', '-uall')
assert status, 'Expected the preserved tested draft'
assert all(line[3:] in inputs for line in status.splitlines()), 'Unexpected worktree changes'
assert not git('diff', '--cached', '--name-only'), 'Unexpected staged changes'
assert git('ls-remote', 'origin', 'refs/heads/main') == expected + '\trefs/heads/main'
git('fetch', 'origin', 'refs/heads/main:refs/remotes/origin/main')
assert git('rev-parse', 'origin/main') == expected

# The snapshot remains available after integration. No stash is dropped.
subprocess.run(['git', 'stash', 'push', '-u', '-m', 'Preserve qualified actor pixel draft', '--', *inputs], cwd=root, check=True)
stash = git('rev-parse', 'refs/stash')
assert not git('status', '--porcelain=v1', '-uall')
git('merge', '--ff-only', 'origin/main')
assert git('rev-parse', 'HEAD') == expected
manifest = json.loads((project/'evidence/actor-pixels-2026-10-08/source-sha256.json').read_text())
for path, digest in manifest['files'].items():
    assert hashlib.sha256((root/path).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest, path
assert not git('status', '--porcelain=v1', '-uall')
receipt = dict(host=socket.gethostname(), commit=expected, preserved_stash=stash, checked_files=len(manifest['files']), mismatches=[], clean=True)
(project/'local/actor-pixels-integration.json').write_text(json.dumps(receipt, indent=2)+'\n')
print(json.dumps(receipt, indent=2))
