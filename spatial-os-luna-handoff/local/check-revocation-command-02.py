"""Correct the binding build and use only the twelve new command cases."""
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
old = base / 'm02-revocation-command-01'
root = base / 'm02-revocation-command-02'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
archive = base / 'revocation-command-source-02.tar'
assert sha(archive) == sys.argv[1]
root.mkdir(exist_ok=False)
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
original = json.loads((old / 'manifest.json').read_bytes())
assert set(manifest['files']) == set(original['files'])
assert [name for name in manifest['files'] if manifest['files'][name] != original['files'][name]] == ['tests/core/test_revocation_command.py']
source = root / 'source'
def audit():
    for place, inputs in ((root, manifest), (old, original)):
        assert all(sha(place / 'source' / name) == digest for name, digest in inputs['files'].items())
        assert all(inputs['files'][name] == digest for name, digest in inputs['locked_files'].items())
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try: name = (path / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        assert name not in ('ctest', 'ninja', 'cc1', 'cc1plus', 'clang', 'clang++'), (path, name)

cases = [('command', 'test_revocation_command.py', ['identityd', 'spatial-issuer-client', 'spatial-revoke']),
    ('revocation', 'test_identity_revocation.py', ['identityd']),
    ('barrier', 'test_identity_participant_barrier.py', ['identityd']),
    ('barrier-client', 'test_identity_participant_barrier_client.py', ['identityd', 'spatial-issuer-client']),
    ('issuer-client', 'test_issuer_client.py', ['identityd', 'spatial-issuer-client'])]
for profile in ('debug', 'runtime-sanitize'):
    out = root / 'checks' / profile; out.mkdir(parents=True, exist_ok=False)
    build = old / profile / 'build'
    env = dict(os.environ, CCACHE_BASEDIR=str(old), SPATIAL_COMMAND_EVIDENCE=str(out / 'commands'),
        PYTHONPATH=str(build / 'python') + ':' + str(source))
    record = dict(profile=profile, host='sb16', status='running', started=time.time(), commands=[],
        compiled_inputs=original['files'], checked_inputs=manifest['files'])
    def save(): (out / 'review.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())
    commands = []
    if profile == 'runtime-sanitize':
        commands.append(('configure', ['cmake', '-S', str(old / 'source'), '-B', str(build), '-G', 'Ninja',
            '-DCMAKE_BUILD_TYPE=Debug', '-DSPATIAL_INPUT_TESTS=ON', '-DSPATIAL_SANITIZERS=ON',
            '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache', '-DCMAKE_C_COMPILER_LAUNCHER=ccache']))
    commands.append(('build', ['cmake', '--build', str(build), '--parallel', '2', '--target',
        'identityd', 'spatial-issuer-client', 'spatial-revoke', 'issuer-python-protobuf', 'python-protobuf',
        'security-python-protobuf', 'key-python-protobuf', 'grants-python-protobuf']))
    commands += [(label, ['python3', str(source / 'tests/core' / script)] + [str(build / name) for name in binaries])
        for label, script, binaries in cases]
    save()
    try:
        for label, command in commands:
            audit()
            item = dict(label=label, argv=command, started=time.time())
            record['commands'].append(item); save()
            with (out / (label + '.log')).open('xb') as log:
                result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
            item.update(exit_code=result.returncode, ended=time.time(), sha256=sha(out / (label + '.log')))
            if label == 'build':
                record['binaries'] = {name: sha(build / name) for name in ('identityd', 'spatial-issuer-client', 'spatial-revoke')}
            if label in {case[0] for case in cases}:
                text = (out / (label + '.log')).read_text()
                count = re.findall(r'Ran (\d+) tests?', text)
                item['count'] = int(count[0]) if len(count) == 1 else None
            save(); print(profile, label, result.returncode, item.get('count'), flush=True)
            if result.returncode:
                print((out / (label + '.log')).read_text()[-14000:], flush=True)
                raise RuntimeError(label + ' failed')
            if label in {case[0] for case in cases}:
                assert item['count'] and '\nOK\n' in text and 'skipped=' not in text
        audit()
        assert all(sha(build / name) == digest for name, digest in record['binaries'].items())
        record.update(status='passed', cases=sum(item.get('count', 0) for item in record['commands']))
    except BaseException:
        record['status'] = 'failed'
        raise
    finally:
        record['ended'] = time.time(); save()

print('Both profiles passed. Preserve evidence before integration.', flush=True)
