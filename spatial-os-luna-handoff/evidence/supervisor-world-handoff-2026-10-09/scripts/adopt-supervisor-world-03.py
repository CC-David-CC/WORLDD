"""Adopt qualified world source without restarting services or migrating state."""
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
assert host in ('sb16', 'sb16-2') and len(sys.argv) == 2
expected = sys.argv[1]
assert len(expected) == 40 and all(c in '0123456789abcdef' for c in expected)
def git(*args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
assert git('status', '--porcelain') == '' and git('branch', '--show-current') == 'main'
assert git('remote', 'get-url', 'origin') == 'dflanag3@box-git:/srv/data/git/spacial_desktop.git'
workers = {'qualify-world-handoff-02.py', 'diagnose-supervised-world-participation-01.py'}
for p in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        if p.parent.stat().st_uid != os.geteuid():
            continue
        argv = p.read_bytes().split(b'\0')
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        continue
    if len(argv) >= 2 and Path(os.fsdecode(argv[0])).name.startswith('python'):
        assert Path(os.fsdecode(argv[1])).name not in workers, 'World qualification is still active'
qualified = json.loads((r / 'local/supervisor-world-handoff-candidate-02.json').read_text())['files']
assert len(qualified) == 290
summary = json.loads((r / ('local/world-handoff-summary-final-' + host + '-02.json')).read_text())
assert summary['host'] == host and summary['candidate'] == 2 and summary['source_files'] == 290
for profile in ('debug', 'runtime-sanitize'):
    result = summary[profile]
    assert tuple(result[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped')) == (1407,95,51,151,0,0)
    build = r / 'local/m02-supervisor-world-handoff-02' / profile / 'build'
    assert all(sha(build / n) == digest for n, digest in result['binaries'].items())
assert all(sha(r / 'local/m02-supervisor-world-handoff-02/source' / n) == digest for n, digest in qualified.items())
before = git('rev-parse', 'HEAD')
subprocess.run(['git','-C',str(repo),'fetch','origin','main'],check=True)
assert git('rev-parse', 'origin/main') == expected
record = json.loads(git('show',expected + ':spatial-os-luna-handoff/evidence/supervisor-world-handoff-2026-10-09/integration-applied-03.json'))
assert record['integrated'] and record['qualified_files'] == qualified
manifest = record['files']
name = 'proto/spatial/supervisor/v1/launch.proto'
assert set(manifest) == set(qualified) and [n for n in manifest if manifest[n] != qualified[n]] == [name]
comment = json.loads((r / 'local/world-comment-equivalence-03/result.json').read_text())
assert comment['comments_only'] and comment['descriptor_equal']
assert comment['before_sha256'] == qualified[name] and comment['after_sha256'] == manifest[name]
for label in ('before','after'):
    assert sha(r / 'local/world-comment-equivalence-03' / (label + '.pb')) == comment['descriptor_sha256']
# Check every target Git object before changing the checkout.
for n, digest in manifest.items():
    data = subprocess.check_output(['git','-C',str(repo),'show',expected+':spatial-os-luna-handoff/'+n])
    assert hashlib.sha256(data).hexdigest() == digest, n
subprocess.run(['git','-C',str(repo),'merge','--ff-only',expected],check=True,stdout=subprocess.DEVNULL)
assert all(sha(r / n) == digest for n, digest in manifest.items())
assert git('rev-parse','HEAD') == expected and git('status','--porcelain') == ''
receipt = dict(host=host,before=before,commit=expected,source_hashes_matched=290,
               qualified_candidate=2,comment_only_delta=name,descriptor_equivalence_verified=True,
               worktree_clean=True,runtime_restarted=False,operator_state_migrated=False)
(r / ('local/supervisor-world-adoption-'+host+'-03.json')).write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
