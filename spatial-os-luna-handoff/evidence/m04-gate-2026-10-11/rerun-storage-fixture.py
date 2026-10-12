"""Reassess only storage fixture failures using unchanged qualified binaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0]
    assert host in ('sb16', 'sb16-2')
    original = Path('/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff')
    corrected = Path('/home/dflanag3/src/spacial_desktop-m04-fixture-20261011/spatial-os-luna-handoff')
    gate_path = original / 'local/m04-full-gate-01/run.json'
    gate = json.loads(gate_path.read_text())
    assert gate['candidate'] == 'f74c6b579c6149f41e1cbd17b025eb92fd453009'
    revision = subprocess.check_output(['git', '-C', str(corrected), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision.startswith('72a9d6b')
    changed = []
    for name, expected in gate['source_sha256'].items():
        assert digest(original / name) == expected, 'Original changed: ' + name
        if digest(corrected / name) != expected:
            changed.append(name)
    assert changed == ['tests/native/storage_session_volume.py'], changed
    if args.check_only:
        print('Only the intended fixture helper differs; no tests started.')
        return 0
    assert gate['status'] != 'running', 'Wait for the existing gate to finish'
    assert set(gate['profiles']) == {'Debug', 'ASan-UBSan'}
    for name, expected in gate['installed_helpers'].items():
        assert digest(Path(name)) == expected
    vendor = Path(json.loads(Path(gate['graphics']['__EGL_VENDOR_LIBRARY_FILENAMES']).read_text())['ICD']['library_path'])
    assert digest(vendor) == gate['vendor_sha256']
    out = corrected / 'local/m04-storage-fixture-correction-01'
    out.mkdir(parents=True, exist_ok=False)
    record = dict(status='running', host=host, phase_complete=False, original_candidate=gate['candidate'],
                  corrected_candidate=revision, gate_sha256=digest(gate_path), changed_files=changed,
                  corrected_helper_sha256=digest(corrected / changed[0]), results=[], runner_pid=os.getpid())
    def save():
        (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    save()
    try:
        for profile in ('Debug', 'ASan-UBSan'):
            row = gate['profiles'][profile]
            assert row['status'] != 'running'
            build = original / 'local/m04-gate-m04-full-gate-01' / profile
            assert row['binary_sha256']
            for name, expected in row['binary_sha256'].items():
                assert digest(build / name) == expected
            assert digest(Path(row['server']['path'])) == row['server']['sha256']
            assert digest(Path(row['launcher'])) == row['launcher_sha256']
            inventory = json.loads((gate_path.parent / profile / 'inventory.log').read_text())
            tests = [t for t in inventory['tests'] if t['name'] in
                     ('session-storage-owner', 'session-storage-contention')]
            assert len(tests) == 2
            for test in tests:
                target = out / profile / test['name']
                target.mkdir(parents=True)
                properties = {p['name']: p['value'] for p in test['properties']}
                env = dict(os.environ)
                env.update(item.split('=', 1) for item in properties.get('ENVIRONMENT', []))
                env.update(PYTHONPATH=str(build / 'python') + ':' + str(corrected),
                           SPATIAL_SECURITY_EVIDENCE=str(target / 'native'),
                           SPATIAL_SCOPE_EVIDENCE=str(target / 'scopes'),
                           SPATIAL_RENDER_EVIDENCE=str(target / 'graphics'))
                for name in ('DISPLAY', 'SPATIAL_TEST_DISPLAY', 'SPATIAL_SECURITY_TEST_CORE_ONLY',
                             'LD_LIBRARY_PATH', '__EGL_VENDOR_LIBRARY_FILENAMES'):
                    env.pop(name, None)
                command = []
                for item in test['command']:
                    if item == str(original):
                        item = str(corrected)
                    elif item.startswith(str(original) + '/') and not item.startswith(str(original / 'local') + '/'):
                        item = str(corrected / Path(item).relative_to(original))
                    command.append(item)
                started = time.monotonic()
                with (target / 'test.log').open('wb') as log:
                    result = subprocess.run(command, cwd=build, env=env, stdout=log,
                        stderr=subprocess.STDOUT, timeout=properties['TIMEOUT'])
                text = (target / 'test.log').read_text()
                count = re.search(r'Ran (\d+) tests? in', text)
                passed = (result.returncode == 0 and count is not None and
                          re.search(r'^OK\s*$', text, re.MULTILINE) is not None and
                          not re.search(r'^FAIL:|^ERROR:|ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:', text, re.MULTILINE))
                record['results'].append(dict(profile=profile, suite=test['name'], command=command,
                    exit_code=result.returncode, passed=bool(passed), checks=int(count[1]) if count else None,
                    seconds=time.monotonic()-started, log_sha256=digest(target / 'test.log')))
                save()
                print(profile, test['name'], 'passed' if passed else 'FAILED', flush=True)
        assert digest(gate_path) == record['gate_sha256']
        assert all(digest(original / name) == expected for name, expected in gate['source_sha256'].items())
        record['status'] = 'passed_needs_primary_review' if all(r['passed'] for r in record['results']) else 'failed'
    except BaseException as error:
        record.update(status='failed', error=repr(error))
        raise
    finally:
        save()
    return 0 if record['status'] == 'passed_needs_primary_review' else 1

if __name__ == '__main__':
    raise SystemExit(main())
