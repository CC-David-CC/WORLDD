"""Build and qualify the selected command increment on sb16 only."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import tarfile
import time

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-revocation-command-01'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
if not root.exists():
    archive = base / 'revocation-command-source-01.tar'
    assert sha(archive) == sys.argv[1]
    root.mkdir()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert len(members) == len({member.name for member in members})
        for member in members:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            path = root.joinpath(*name.parts)
            assert path.resolve().is_relative_to(root.resolve())
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
manifest = json.loads((root / 'manifest.json').read_bytes())
source = root / 'source'

def audit():
    assert all(sha(source / name) == digest for name, digest in manifest['files'].items())
    assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try: name = (path / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        assert name not in ('ctest', 'ninja', 'cc1', 'cc1plus', 'clang', 'clang++'), (path, name)

for profile in ('debug', 'runtime-sanitize'):
    out = root / 'checks' / profile; out.mkdir(parents=True, exist_ok=False)
    build = root / profile / 'build'
    env = dict(os.environ, CCACHE_BASEDIR=str(root), SPATIAL_COMMAND_EVIDENCE=str(out / 'commands'))
    record = dict(profile=profile, host='sb16', status='running', started=time.time(), commands=[])
    def save(): (out / 'review.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())
    commands = [
        ('configure', ['cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Debug',
            '-DSPATIAL_INPUT_TESTS=ON', '-DSPATIAL_SANITIZERS=' + ('ON' if profile == 'runtime-sanitize' else 'OFF'),
            '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache']),
        ('build', ['cmake', '--build', str(build), '--parallel', '2', '--target',
            'identityd', 'spatial-issuer-client', 'spatial-revoke', 'issuer-python-protobuf']),
        ('focused', ['ctest', '--test-dir', str(build), '--output-on-failure', '-V', '-R',
            '^(' + '|'.join(manifest['selected_suites']) + ')$'])]
    save()
    try:
        for label, command in commands:
            audit()
            item = dict(label=label, argv=command, started=time.time())
            record['commands'].append(item); save()
            with (out / (label + '.log')).open('xb') as log:
                result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
            item.update(exit_code=result.returncode, ended=time.time(), sha256=sha(out / (label + '.log')))
            save(); print(profile, label, result.returncode, flush=True)
            if result.returncode:
                print((out / (label + '.log')).read_text()[-9000:], flush=True)
                raise RuntimeError(label + ' failed')
        audit()
        log = (out / 'focused.log').read_text()
        counts = list(map(int, re.findall(r'Ran (\d+) tests?', log)))
        assert len(counts) == len(manifest['selected_suites']) and all(counts)
        assert '100% tests passed' in log and 'skipped=' not in log and 'expected failures=' not in log
        record.update(status='passed', suites=len(counts), cases=sum(counts),
            binaries={name: sha(build / name) for name in ('identityd', 'spatial-issuer-client', 'spatial-revoke')})
    except BaseException:
        record['status'] = 'failed'
        raise
    finally:
        record['ended'] = time.time(); save()

attachments = ['manifest.json']
attachments += [str(path.relative_to(root)) for path in (root / 'checks').rglob('*') if path.is_file()]
attachments += ['source/' + name for name in manifest['changed']]
archive = base / 'revocation-command-evidence-01.tar'
with tarfile.open(archive, 'x') as tar:
    for name in sorted(attachments):
        path = root / name
        assert path.suffix in ('.json', '.log', '.pb', '.txt', '.cpp', '.py', '.proto')
        assert b'PRIVATE KEY-----' not in path.read_bytes() and b'Bearer fleet-' not in path.read_bytes()
        tar.add(path, arcname=name, recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), attachments=len(attachments))), flush=True)
