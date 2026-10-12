"""Repeat changed-path bypass witnesses in isolated server source copies."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--dependencies', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
    gate = json.loads((root / 'local/m04-full-gate-01/run.json').read_text())
    if not args.check_only:
        assert gate['status'] == 'commands_passed_needs_review', 'Wait for existing local gate'
    assert gate['candidate'] == 'f74c6b579c6149f41e1cbd17b025eb92fd453009'
    for name, expected in gate['source_sha256'].items():
        assert digest(root / name) == expected, name
    lock = json.loads((root / 'server/base.lock.json').read_text())
    files = ['server/base.lock.json', 'tools/build_xserver.py', *lock['patches'], *lock['overlays'],
             *[str(p.relative_to(root)) for p in (root / 'tests/native').glob('*.py')]]
    specifications = [
        ('authentication', 'spatialsecurity.c',
         'if (!match) return denial;', 'if (!match) return NULL;',
         'test_security_identity.py', 'SecurityIdentityTests',
         'test_bad_cookies_cannot_use_disabled_host_access_control'),
        ('issuer-disconnect', 'spatialsecurity.c',
         'if (grants[i].active && grants[i].issuer == state->connection) grants[i].active = FALSE;',
         'if (FALSE && grants[i].active && grants[i].issuer == state->connection) grants[i].active = FALSE;',
         'test_security_identity.py', 'SecurityIdentityTests',
         'test_issuing_connection_loss_revokes_its_credentials'),
        ('resource-access', 'spatialpolicy.c',
         'XaceResourceAccessRec *rec = data;', 'XaceResourceAccessRec *rec = data; return;',
         'test_resource_isolation.py', 'ResourceIsolationTests', 'test_foreign_pixels_cannot_be_read'),
        ('window-pixels', 'spatialpolicy.c',
         'if (rec->rtype == PictureType && (rec->access_mode & DixReadAccess)) {',
         'if (FALSE && rec->rtype == PictureType && (rec->access_mode & DixReadAccess)) {',
         'test_extension_isolation.py', 'ExtensionIsolationTests',
         'test_render_shape_hole_is_not_a_foreign_pixel_source'),
    ]
    for name, source, before, after, suite, cls, test in specifications:
        assert (root / 'server/xlibre' / source).read_text().count(before) == 1, name
        assert 'def ' + test + '(' in (root / 'tests/native' / suite).read_text(), test
    if args.check_only:
        print('Four mutation anchors and witnesses verified; no build or test started.')
        return 0
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    profile = gate['profiles']['Debug']
    build = root / 'local/m04-gate-m04-full-gate-01/Debug'
    for name, expected in profile['binary_sha256'].items():
        assert digest(build / name) == expected, name
    vendor = Path(json.loads(Path(gate['graphics']['__EGL_VENDOR_LIBRARY_FILENAMES']).read_text())['ICD']['library_path'])
    assert digest(vendor) == gate['vendor_sha256']
    env = {**os.environ, **gate['graphics'], 'PYTHONPATH': str(build / 'python'),
           'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1',
           'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}
    env.pop('SPATIAL_SECURITY_TEST_CORE_ONLY', None)
    record = dict(status='running', candidate=gate['candidate'], phase_complete=False, results=[])
    try:
        for name, source, before, after, suite, cls, test in specifications:
            target = out / name
            isolated = target / 'source'
            for item in files:
                destination = isolated / item
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((root / item).read_bytes())
            local = isolated / 'local'
            local.mkdir()
            for dep in ('xlibre', 'xorgproto', 'xlibre-deps'):
                dependency = (args.dependencies / dep).resolve()
                assert dependency.is_dir(), dependency
                (local / dep).symlink_to(dependency, target_is_directory=True)
            policy = isolated / 'server/xlibre' / source
            original = policy.read_text()
            modified = original.replace(before, after)
            policy.write_text(modified)
            (target / 'mutation.patch').write_text(''.join(difflib.unified_diff(
                original.splitlines(True), modified.splitlines(True), fromfile=source, tofile=source)))
            with (target / 'build.log').open('wb') as log:
                subprocess.run(['python3', str(isolated / 'tools/build_xserver.py'),
                                '--evidence-dir', str(target / 'build')],
                               env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200, check=True)
            receipt = json.loads((target / 'build/run.json').read_text())
            server = receipt['binaries']['Xvfb']
            assert digest(Path(server['path'])) == server['sha256']
            child = {**env, 'SPATIAL_TEST_XSERVER': server['path'],
                     'SPATIAL_SECURITY_EVIDENCE': str(target / 'native')}
            with (target / 'witness.log').open('wb') as log:
                result = subprocess.run(['python3', str(isolated / 'tests/native' / suite),
                    str(build / 'spatial-security-auth'), cls + '.' + test],
                    env=child, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            text = (target / 'witness.log').read_text()
            failures = re.findall(r'^FAIL: (\S+)', text, re.MULTILINE)
            detected = (result.returncode == 1 and failures == [test]
                        and not re.search(r'^ERROR:', text, re.MULTILINE)
                        and re.search(r'Ran 1 test\b', text) is not None)
            record['results'].append(dict(name=name, detected=detected, server=server,
                witness=test, failures=failures, log_sha256=digest(target / 'witness.log')))
            assert detected, 'Mutation did not produce its exact expected assertion: ' + name
            print(name, 'detected', flush=True)
        assert all(digest(root / name) == expected for name, expected in gate['source_sha256'].items())
        record['status'] = 'passed_needs_primary_review'
    except BaseException as error:
        record.update(status='failed', error=repr(error))
        raise
    finally:
        (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
