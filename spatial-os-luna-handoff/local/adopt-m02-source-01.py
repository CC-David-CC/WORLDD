"""Verify and fast-forward reviewed source. Never restart runtime or discard local changes."""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import time

assert len(sys.argv) == 3 and sys.argv[1] in ('--check', '--apply')
mode, expected = sys.argv[1:]
assert len(expected) == 40 and all(c in '0123456789abcdef' for c in expected)
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
repo = Path('/home/dflanag3/src/spacial_desktop')
project = repo / 'spatial-os-luna-handoff'
base = project / 'local'
lock = (base / 'm02-phase-qualification.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
root = base / 'm02-source-adoption-01'
root.mkdir(exist_ok=True)
receipt = root / ('check.json' if mode == '--check' else 'applied.json')
assert not receipt.exists(), 'Preserve the existing receipt; do not repeat a completed adoption'
sha = lambda data: hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()


def workloads():
    found = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name) == os.getpid():
            continue
        try:
            name = (path / 'comm').read_text().strip()
            exe = os.readlink(path / 'exe')
            args = (path / 'cmdline').read_bytes()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        managed_job = str(base).encode() + b'/' in args and any(
            token in args for token in (b'/qualify-', b'/run-m02-', b'/diagnose-', b'/trace-qualification-'))
        if name in ('ctest', 'cc1', 'cc1plus', 'clang', 'clang++', 'ninja', 'bpftrace') or managed_job or exe.startswith(str(repo) + '/'):
            found.append(dict(pid=int(path.name), name=name, executable=exe))
    return found


assert git('branch', '--show-current') == 'main'
assert git('remote', 'get-url', 'origin') == 'dflanag3@box-git:/srv/data/git/spacial_desktop.git'
assert git('status', '--porcelain') == '', 'Local checkout changes require review'
assert not workloads(), 'A project runtime, compiler, or qualification job is active'
before = git('rev-parse', 'HEAD')
if mode == '--check':
    with (root / 'fetch.log').open('xb') as log:
        subprocess.run(['git', '-C', str(repo), 'fetch', 'origin', 'm02/control-recovery'],
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    assert git('rev-parse', 'FETCH_HEAD') == expected
else:
    checked = json.loads((root / 'check.json').read_bytes())
    assert checked['status'] == 'source_verified_ready' and checked['commit'] == expected and checked['before'] == before
    assert git('rev-parse', 'origin/main') == expected, 'Fetch the integrated main before adoption'
subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', before, expected], check=True)
integration_path = 'spatial-os-luna-handoff/evidence/m02-gate-2026-10-10/integration-ready-01.json'
integration_bytes = subprocess.check_output(['git', '-C', str(repo), 'show', expected + ':' + integration_path])
integration = json.loads(integration_bytes)
assert integration['status'] == 'reviewed_source_ready_for_adoption'
assert len(integration['source_files']) == 352 and len(integration['locked_files']) == 41
gate = base / 'm02-phase-gate-02'
manifest = json.loads((gate / 'manifest.json').read_bytes())
qualified = json.loads((gate / 'summary.json').read_bytes())
assert manifest['commit'] == integration['runtime_source_commit']
assert manifest['locked_files'] == integration['locked_files']
assert all(sha((gate / 'source' / name).read_bytes()) == digest for name,digest in manifest['files'].items())
for profile in ('debug', 'runtime-sanitize'):
    assert all(sha((gate / profile / 'build' / name).read_bytes()) == digest
               for name,digest in qualified['profiles'][profile]['binaries'].items())
assert sha(Path(qualified['server']['path']).read_bytes()) == qualified['server']['sha256']


def blobs(names):
    request = ''.join(expected + ':' + name + '\n' for name in names).encode()
    data = io.BytesIO(subprocess.check_output(['git', '-C', str(repo), 'cat-file', '--batch'], input=request))
    result = {}
    for name in names:
        header = data.readline().split()
        assert len(header) == 3 and header[1] == b'blob', name
        result[name] = data.read(int(header[2]))
        assert data.read(1) == b'\n'
    return result


sources = {'spatial-os-luna-handoff/' + name:digest for name,digest in integration['source_files'].items()}
evidence = {'spatial-os-luna-handoff/' + name:digest for name,digest in integration['evidence'].items()}
for name, data in blobs(sorted(sources.keys() | evidence.keys())).items():
    assert sha(data) == (sources | evidence)[name], name
for name, digest in integration['locked_files'].items():
    assert sources['spatial-os-luna-handoff/' + name] == digest

old_names = set(git('ls-tree', '-r', '--name-only', before).splitlines())
new_names = set(git('ls-tree', '-r', '--name-only', expected).splitlines())
colliding = sorted(name for name in new_names - old_names if (repo / name).exists())
collisions = []
for name, data in blobs(colliding).items():
    path = repo / name
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(repo)
    digest = sha(path.read_bytes())
    if digest != sha(data):
        subprocess.run(['git', '-C', str(repo), 'check-ignore', '-q', '--', name], check=True)
        collisions.append(dict(path=name, old_sha256=digest, target_sha256=sha(data)))
record = dict(host=host, before=before, commit=expected, checked_at=time.time(),
              integration_record_sha256=sha(integration_bytes), source_files_verified=len(sources),
              locked_files_verified=41, identical_existing_untracked_files=len(colliding)-len(collisions),
              different_ignored_collisions=collisions, runtime_restarted=False, operator_state_migrated=False)
if mode == '--check':
    record['status'] = 'source_verified_ready'
else:
    assert collisions == checked['different_ignored_collisions'], 'Ignored files changed after review'
    if collisions:
        backup = root / 'prior-ignored-files.tar'
        with tarfile.open(backup, 'x') as archive:
            for item in collisions:
                archive.add(repo / item['path'], arcname=item['path'], recursive=False)
        record['collision_backup_sha256'] = sha(backup.read_bytes())
        record['collision_backup_path'] = str(backup)
    assert not workloads() and git('status', '--porcelain') == ''
    subprocess.run(['git', '-C', str(repo), 'read-tree', '--dry-run', '-m', '-u', 'HEAD', expected], check=True)
    with (root / 'merge.log').open('xb') as log:
        subprocess.run(['git', '-C', str(repo), 'merge', '--ff-only', expected],
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    assert git('rev-parse', 'HEAD') == expected and git('status', '--porcelain') == ''
    assert all(sha((repo / name).read_bytes()) == digest for name,digest in sources.items())
    record.update(status='reviewed_source_adopted', worktree_clean=True, source_verified_after=True)
receipt.write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
