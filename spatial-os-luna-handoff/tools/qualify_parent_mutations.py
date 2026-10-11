#!/usr/bin/env python3
"""Detect three parent-metadata bypasses in isolated native-server builds."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mutation_result(text, exit_code, expected):
    failures = set(re.findall(r'^FAIL: (\S+)', text, re.MULTILINE))
    errors = set(re.findall(r'^ERROR: (\S+)', text, re.MULTILINE))
    count = re.search(r'Ran (\d+) tests', text)
    detected = (exit_code != 0 and failures == expected and not errors
                and count is not None and int(count[1]) == 28)
    return bool(detected), failures, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--environment-receipt', type=Path, required=True)
    parser.add_argument('--runtime-build', type=Path, required=True)
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] not in ('sb16', 'sb16-2'):
        parser.error('Native mutation checks require an authorized build host')
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    work = ROOT / 'local' / ('parent-mutations-' + evidence.name)
    work.mkdir(exist_ok=False)
    lock = json.loads((ROOT / 'server/base.lock.json').read_text())
    files = ['server/base.lock.json', 'tools/build_xserver.py',
             *lock['patches'], *lock['overlays'],
             *[str(p.relative_to(ROOT)) for p in (ROOT / 'tests/native').glob('*.py')]]
    before = {name: digest(ROOT / name) for name in files}
    environment = json.loads(args.environment_receipt.read_text())['environment']
    env = {**os.environ, **environment}
    mutations = {
        'query-parent': {'test_query_tree_hides_foreign_parent',
                         'test_swapped_query_tree_hides_foreign_parent'},
        'reparent-event': {'test_reparent_notify_hides_foreign_parent',
                           'test_swapped_reparent_notify_hides_foreign_parent'},
        'child-event': {'test_' + prefix + 'foreign_child_' + action + '_event_is_not_delivered'
                        for prefix in ('', 'swapped_')
                        for action in ('create', 'map', 'unmap', 'destroy', 'configure', 'reparent')}}
    record = {'phase_complete': False, 'inputs_sha256': before,
              'environment': environment, 'mutations': []}
    for name, expected in mutations.items():
        isolated = work / name
        for source in files:
            destination = isolated / source
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / source).read_bytes())
        local = isolated / 'local'
        local.mkdir()
        for dependency in ('xlibre', 'xorgproto', 'xlibre-deps'):
            (local / dependency).symlink_to((ROOT / 'local' / dependency).resolve(), target_is_directory=True)
        if name == 'query-parent':
            altered = dict(lock)
            altered['patches'] = [p for p in lock['patches'] if p != 'server/patches/0010-private-parent-metadata.patch']
            (isolated / 'server/base.lock.json').write_text(json.dumps(altered, indent=2) + '\n')
        else:
            policy = isolated / 'server/xlibre/spatialpolicy.c'
            original = policy.read_text()
            guard = ('if ((event->u.u.type & 0x7f) == ReparentNotify)' if name == 'reparent-event'
                     else 'if (subject != None && !SpatialSecurityVisibleResource(rec->client, subject))')
            if original.count(guard) != 1:
                raise ValueError('Mutation guard does not match exactly once')
            policy.write_text(original.replace(guard, guard.replace('if (', 'if (FALSE && ', 1)))
        output = evidence / name
        output.mkdir()
        command = ['python3', str(isolated / 'tools/build_xserver.py'),
                   '--evidence-dir', str(output / 'build')]
        with (output / 'build.log').open('wb') as log:
            build = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        if build.returncode:
            raise RuntimeError('Mutation build failed: ' + name)
        receipt = json.loads((output / 'build/run.json').read_text())
        server = Path(receipt['binaries']['Xvfb']['path'])
        if digest(server) != receipt['binaries']['Xvfb']['sha256']:
            raise ValueError('Mutation server bytes changed')
        child = {**env, 'SPATIAL_TEST_XSERVER': str(server),
                 'SPATIAL_SECURITY_EVIDENCE': str(output / 'native-server'),
                 'PYTHONPATH': str(args.runtime_build.resolve() / 'python')}
        command = ['python3', str(isolated / 'tests/native/test_parent_metadata.py'),
                   str(args.runtime_build.resolve() / 'spatial-security-auth')]
        with (output / 'test.log').open('wb') as log:
            result = subprocess.run(command, env=child, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        text = (output / 'test.log').read_text()
        detected, failures, errors = mutation_result(text, result.returncode, expected)
        record['mutations'].append({'name': name, 'expected_failures': sorted(expected),
            'observed_failures': sorted(failures), 'detected': bool(detected),
            'observed_errors': sorted(errors),
            'exit_code': result.returncode, 'server': receipt['binaries']['Xvfb'],
            'policy_sha256': digest(isolated / 'server/xlibre/spatialpolicy.c'),
            'lock_sha256': digest(isolated / 'server/base.lock.json')})
        (evidence / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
        print(name, 'detected' if detected else 'FAILED', flush=True)
        if not detected:
            raise RuntimeError('Mutation was not detected by its exact counterexamples')
    record['source_unchanged'] = all(digest(ROOT / name) == expected for name, expected in before.items())
    record['status'] = 'passed' if record['source_unchanged'] else 'invalidated'
    (evidence / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
