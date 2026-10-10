"""Freeze the focused broker-cleanup increment and preserve its exit baseline."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
archive = root / 'local/session-launch-exit-baseline-01-sb16.tar.gz'
assert sha(archive.read_bytes()) == '337db2508598fca37b430da62f7c061ec537b1b75b32571d772e58338da54c82'
evidence = root / 'evidence/control-recovery-2026-10-09'
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({m.name for m in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name.startswith('session-launch-exit-baseline01/sb16/')
    for member in members:
        path = evidence.joinpath(*PurePosixPath(member.name).parts)
        assert path.resolve().is_relative_to(evidence.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        content = tar.extractfile(member).read()
        if path.exists(): assert path.read_bytes() == content
        else:
            with path.open('xb') as stream: stream.write(content)
baseline = evidence / 'session-launch-exit-baseline01/sb16'
report = json.loads((baseline / 'baseline-review.json').read_bytes())
assert report['status'] == 'expected_exit_status_failure'
assert all(sha((baseline / n).read_bytes()) == h for n, h in report['attachments'].items())

previous = json.loads((evidence / 'coordinator-retirement-02/sb16/checks/review.json').read_bytes())
for name in ('src/testing/session_launch_exit_probe.cpp', 'tests/core/test_session_launch_exit.py',
             'tests/native/test_session_broker_cleanup.py'):
    assert name not in previous['source']
    previous['source'][name] = ''
files = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in previous['source']}
assert len(files) == 340
assert all(sha(files[name]) == digest for name, digest in previous['locked'].items())
manifest = dict(files={n: sha(v) for n, v in files.items()}, locked_files=previous['locked'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    purpose='Retain checked broker cleanup after supervisor exit. Preserve rejected exit status across retries. Stop exact owned world scopes after broker loss without claiming reservation recovery or issuer retirement.',
    full_gate='M02 qualification remains pending on both hosts.')
data = (json.dumps(manifest, indent=2) + '\n').encode()
with (root / 'local/session-broker-cleanup-source-01.json').open('xb') as stream: stream.write(data)
with tarfile.open(root / 'local/session-broker-cleanup-source-01.tar', 'x') as tar:
    for name, content in [('manifest.json', data)] + [('source/' + n, v) for n, v in files.items()]:
        item = tarfile.TarInfo(name); item.size = len(content); item.mode = 0o644
        tar.addfile(item, io.BytesIO(content))
digest = sha((root / 'local/session-broker-cleanup-source-01.tar').read_bytes())
unpack = (root / 'local/unpack-coordinator-retirement-02.py').read_text()
unpack = unpack.replace('coordinator-retirement-source-02', 'session-broker-cleanup-source-01')
unpack = unpack.replace('m02-coordinator-retirement-02', 'm02-session-broker-cleanup-01')
unpack = unpack.replace('bd293dcb7a8598addbbda2a261a89961a03e7a65e299c0355c873e7d6efce178', digest)
unpack = unpack.replace('coordinator-retirement candidate02', 'session-broker-cleanup candidate01')
with (root / 'local/unpack-session-broker-cleanup-01.py').open('xb') as stream: stream.write(unpack.encode())
runner = (root / 'local/check-coordinator-retirement-02.py').read_text()
runner = runner.replace('m02-coordinator-retirement-02', 'm02-session-broker-cleanup-01')
start = runner.index("selection = "); end = runner.index('\nrecord = ', start)
runner = runner[:start] + "selection = '^(session-broker|session-world|session-application|session-dependent-cleanup|session-coordinator-retirement|authority-supervisor|authority-supervisor-crash-fencing|authority-control-binding|supervisor-handoff-lifetime)$'" + runner[end:]
start = runner.index("    purpose="); end = runner.index('\n    full_qualification=', start)
runner = runner[:start] + "    purpose='Real broker and confined application crash cleanup, retained reply delivery, exact retries, unavailable broker during world cleanup, delayed native reply, supervisor death, and remembered rejected exit status. Related suites exercise lifecycle, authorization and dependent recovery boundaries.'," + runner[end:]
runner = runner.replace("run('retirement',", "run('broker-cleanup',")
runner = runner.replace("'^session-coordinator-retirement$'", "'^(supervisor-session-exit|session-broker-cleanup)$'")
with (root / 'local/check-session-broker-cleanup-01.py').open('xb') as stream: stream.write(runner.encode())
print(json.dumps(dict(source_files=len(files), locked_files=len(previous['locked']), source_archive_sha256=digest,
    exit_baseline_attachments=len(report['attachments']))))
