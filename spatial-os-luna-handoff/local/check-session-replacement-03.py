"""Run a Python-only correction against verified unchanged Debug and sanitizer binaries."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-session-replacement-03'; source = root / 'source'
prior_root = base / 'm02-session-replacement-02'
prior = json.loads((prior_root / 'checks/review.json').read_bytes())
manifest = json.loads((root / 'manifest.json').read_bytes())
assert prior['status'] == 'failed' and 'ended' in prior
assert {name for name, digest in manifest['files'].items() if prior['source'].get(name) != digest} == {'tests/native/test_session_replacement.py'}
assert set(manifest['files']) == set(prior['source'])
names = ['session-replacement', 'session-world', 'session-application', 'session-dependent-cleanup',
    'session-coordinator-retirement', 'session-broker-cleanup', 'session-broker-retirement', 'session-stopped-broker-dependency']
selection = '^(' + '|'.join(names[1:]) + ')$'
out = root / 'checks'; out.mkdir(exist_ok=False)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
record = dict(status='testing', host='sb16', started=time.time(), source=manifest['files'],
    locked=manifest['locked_files'], server=prior['server'], profiles={}, selection=selection,
    reused_builds=str(prior_root), build_provenance_sha256=sha(prior_root / 'checks/review.json'),
    purpose='Rerun three corrected native replacement cases and seven related suites with unchanged pinned compiled inputs.',
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

def quote(value):
    assert ']=]' not in str(value)
    return '[=[' + str(value) + ']=]'

save()
try:
    for profile in ('debug', 'runtime-sanitize'):
        idle(); audit()
        build = prior_root / profile / 'build'
        target = out / profile; target.mkdir()
        testdir = target / 'ctest'; testdir.mkdir()
        original = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1',
            '-R', '^(' + '|'.join(names) + ')$']))
        assert {test['name'] for test in original['tests']} == set(names)
        adjusted = []
        content = []
        for test in original['tests']:
            command = [arg.replace(str(prior_root / 'source'), str(source)).replace(
                str(prior_root / 'checks' / profile), str(target)) for arg in test['command']]
            assert all(str(prior_root / 'source') not in arg and str(prior_root / 'checks') not in arg for arg in command)
            properties = {entry['name']: entry['value'] for entry in test['properties']}
            assert set(properties) == {'TIMEOUT', 'WORKING_DIRECTORY'}, properties
            content.append('add_test(' + quote(test['name']) + ' ' + ' '.join(map(quote, command)) + ')')
            content.append('set_tests_properties(' + quote(test['name']) + ' PROPERTIES TIMEOUT ' +
                quote(properties['TIMEOUT']) + ' WORKING_DIRECTORY ' + quote(properties['WORKING_DIRECTORY']) + ')')
            adjusted.append(dict(name=test['name'], command=command, properties=properties))
        (testdir / 'CTestTestfile.cmake').write_text('\n'.join(content) + '\n')
        config = build / 'CMakeCache.txt'
        assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in config.read_text()
        record['profiles'][profile] = dict(reused_build=dict(path=str(build), cache_sha256=sha(config),
            binaries=prior['profiles'][profile]['binaries']), original_tests=original['tests'], selected_tests=adjusted,
            ctest_file_sha256=sha(testdir / 'CTestTestfile.cmake'))
        save()
        env = dict(os.environ, SPATIAL_EVIDENCE_DIR=str(target / 'cases'))
        env.pop('LSAN_OPTIONS', None)
        for label, pattern in [('replacement', '^session-replacement$'), ('focused', selection)]:
            idle(); audit()
            command = ['ctest', '--test-dir', str(testdir), '-V', '-R', pattern]
            entry = dict(command=command, started=time.time())
            record['profiles'][profile][label] = entry; save()
            log = target / (label + '.log')
            with log.open('xb') as stream:
                result = subprocess.run(command, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT)
            entry.update(exit_code=result.returncode, ended=time.time(), log_sha256=sha(log)); save()
            print(profile, label, result.returncode, flush=True)
            if result.returncode:
                print(log.read_text()[-10000:], flush=True)
                raise RuntimeError('Native replacement qualification failed')
            audit()
    record['status'] = 'passed'
except BaseException:
    record['status'] = 'failed'
    raise
finally:
    record['ended'] = time.time(); save()
