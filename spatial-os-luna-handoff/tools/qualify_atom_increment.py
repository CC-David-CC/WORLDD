#!/usr/bin/env python3
"""Run selected M04 regressions against one explicitly built native server."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SUITES = ('atom_privacy', 'security_identity', 'resource_isolation',
          'selection_isolation', 'selection_namespace', 'event_metadata', 'extension_isolation',
          'application_input', 'actor_binding')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-build', type=Path, required=True)
    parser.add_argument('--runtime-build', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--core-only', action='store_true')
    parser.add_argument('--suite', action='append', choices=SUITES)
    args = parser.parse_args()
    suites = args.suite or SUITES
    host = socket.gethostname().split('.')[0]
    if host not in ('sb16', 'sb16-2'):
        parser.error('Native qualification is restricted to sb16 and sb16-2')
    manifest = json.loads(args.server_build.read_text())
    if manifest['status'] != 'passed' or manifest['host'] != host:
        parser.error('A passed native-server build on this host is required')
    server = Path(manifest['binaries']['Xvfb']['path'])
    if digest(server) != manifest['binaries']['Xvfb']['sha256']:
        parser.error('Native server bytes differ from the build receipt')
    for name, expected in manifest['extension_sources'].items():
        if digest(ROOT / name) != expected:
            parser.error('Current server source differs from build receipt: ' + name)
    build = args.runtime_build.resolve(strict=True)
    helper = build / 'spatial-security-auth'
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    inputs = [ROOT / 'tools/qualify_atom_increment.py',
              ROOT / 'tests/native/selection_scope_client.py',
              ROOT / 'tests/native/test_resource_isolation.py',
              ROOT / 'tests/native/test_security_identity.py',
              ROOT / 'tests/native/display_ready.py',
              *[ROOT / ('tests/native/test_' + suite + '.py') for suite in SUITES],
              *[ROOT / name for name in manifest['extension_sources']]]
    before = {str(path.relative_to(ROOT)): digest(path) for path in inputs}
    env = {**os.environ,
           'PYTHONPATH': str(build / 'python') + os.pathsep + str(ROOT),
           'SPATIAL_TEST_XSERVER': str(server),
           'SPATIAL_SECURITY_EVIDENCE': str(evidence / 'native-server'),
           'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1',
           'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}
    env.pop('SPATIAL_SECURITY_TEST_CORE_ONLY', None)
    if args.core_only:
        env['SPATIAL_SECURITY_TEST_CORE_ONLY'] = '1'
    record = {'host': host, 'status': 'running', 'phase_complete': False,
              'native_server_sanitizers': manifest['sanitizers'],
              'core_only': args.core_only, 'source_sha256': before,
              'server': manifest['binaries']['Xvfb'],
              'helper': {'path': str(helper), 'sha256': digest(helper)},
              'environment': {key: value for key, value in env.items()
                              if key.startswith(('SPATIAL', 'ASAN', 'UBSAN')) or key == 'PYTHONPATH'},
              'suites': []}
    try:
        for suite in suites:
            command = ['python3', str(ROOT / ('tests/native/test_' + suite + '.py')), str(helper)]
            log = evidence / (suite + '.log')
            with log.open('wb') as output:
                result = subprocess.run(command, env=env, stdout=output,
                                        stderr=subprocess.STDOUT, timeout=180)
            count = re.search(r'Ran (\d+) tests?', log.read_text(errors='replace'))
            record['suites'].append({'name': suite, 'command': command,
                                     'exit_code': result.returncode,
                                     'count': int(count[1]) if count else None,
                                     'log_sha256': digest(log)})
            (evidence / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
            print(suite, result.returncode, count[1] if count else 'missing count', flush=True)
        unchanged = all(digest(ROOT / name) == expected for name, expected in before.items())
        record['source_unchanged'] = unchanged
        record['status'] = 'passed' if unchanged and all(
            suite['exit_code'] == 0 and suite['count'] for suite in record['suites']) else 'failed'
    except Exception as error:
        record.update(status='failed', error=str(error))
    finally:
        (evidence / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    print(record['status'], evidence, flush=True)
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
