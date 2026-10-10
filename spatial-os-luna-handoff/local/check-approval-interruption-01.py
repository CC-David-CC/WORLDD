"""Explore three new Python scenarios with unchanged qualified compiled inputs."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-approval-interruption-01'; source = root / 'source'
qualified_root = base / 'm02-participant-diagnostics-02'
prior_root = base / 'm02-participant-diagnostics-02'
qualified = json.loads((qualified_root / 'checks/review.json').read_bytes())
prior = json.loads((prior_root / 'checks/review.json').read_bytes())
manifest = json.loads((root / 'manifest.json').read_bytes())
assert qualified['status'] == 'passed'
assert set(manifest['files']) - set(qualified['source']) == {'tests/native/test_approval_interruption.py'}
assert all(manifest['files'][name] == digest for name, digest in qualified['source'].items())
assert {name for name in qualified['source'] if qualified['source'][name] != prior['source'][name]} == set()
out = root / 'checks'; out.mkdir(exist_ok=False)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
record = dict(status='testing', host='sb16', started=time.time(), source=manifest['files'],
    locked=manifest['locked_files'], server=prior['server'], profiles={}, reused_builds=str(prior_root),
    build_provenance_sha256=sha(prior_root / 'checks/review.json'),
    qualified_provenance_sha256=sha(qualified_root / 'checks/review.json'),
    purpose='Three controlled approval interruptions with unchanged qualified production binaries.',
    full_qualification='M02 phase gate remains pending on both hosts.')

def save():
    (out / 'review.json').write_text(json.dumps(record, indent=2) + '\n')

def audit():
    for group in ('files', 'locked_files'):
        assert all(sha(source / name) == digest for name, digest in manifest[group].items())
    assert all(sha(prior_root / 'source' / name) == digest for name, digest in prior['source'].items())
    assert sha(Path(prior['server']['path'])) == prior['server']['sha256']
    for profile in ('debug', 'runtime-sanitize'):
        assert prior['profiles'][profile]['build']['exit_code'] == 0
        assert all(sha(prior_root / profile / 'build' / name) == digest
            for name, digest in prior['profiles'][profile]['binaries'].items())

def idle():
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try: name = (path / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        assert name not in ('ctest', 'ninja', 'cc1', 'cc1plus', 'clang', 'clang++'), (path, name)

save()
try:
    for profile in ('debug', 'runtime-sanitize'):
        idle(); audit()
        build = prior_root / profile / 'build'
        target = out / profile; target.mkdir()
        original = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1',
            '-R', '^authority-supervisor$']))['tests']
        assert len(original) == 1
        command = [arg.replace(str(prior_root / 'source'), str(source)).replace(
            str(prior_root / 'checks' / profile), str(target)).replace(
            'test_authority_supervisor.py', 'test_approval_interruption.py').replace(
            '--unit=spatial-supervisor-', '--unit=spatial-approval-interruption-01-') for arg in original[0]['command']]
        record['profiles'][profile] = dict(original=original[0], command=command,
            binaries=prior['profiles'][profile]['binaries'], started=time.time())
        save()
        env = dict(os.environ, SPATIAL_EVIDENCE_DIR=str(target / 'cases'))
        env.pop('LSAN_OPTIONS', None)
        with (target / 'replacement.log').open('xb') as stream:
            result = subprocess.run(command, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
        record['profiles'][profile].update(exit_code=result.returncode, ended=time.time(),
            log_sha256=sha(target / 'replacement.log'))
        save(); audit()
        print(profile, result.returncode, flush=True)
        if result.returncode:
            print((target / 'replacement.log').read_text()[-14000:], flush=True)
            raise RuntimeError('Approval interruption exploratory failure')
    record['status'] = 'passed'
except BaseException:
    record['status'] = 'failed'
    raise
finally:
    record['ended'] = time.time(); save()
