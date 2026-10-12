"""Validate scoped healthy-setup drafts with pinned gate binaries and original assertions."""
import argparse
import ast
import copy
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
    root = Path('/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff')
    drafts = root / 'local/m04-fixture-correction-01'
    specs = {
        'world-revision-interleave': ('test_world_revision_interleave.py', 'RevisionInterleaveTests', 4),
        'workspace-bootstrap-owner-idle': ('test_workspace_bootstrap_owner_idle.py', 'BootstrapOwnerIdleTests', 1),
    }
    gate_path = root / 'local/m04-full-gate-01/run.json'
    gate = json.loads(gate_path.read_text())
    assert gate['candidate'] == 'f74c6b579c6149f41e1cbd17b025eb92fd453009'
    for name, expected in gate['source_sha256'].items():
        assert digest(root / name) == expected, name
    pins = {}
    for filename, cls, count in specs.values():
        original = ast.parse((root / 'tests/core' / filename).read_text())
        modified = ast.parse((drafts / filename).read_text())
        stripped = copy.deepcopy(modified)
        target = next(n for n in stripped.body if isinstance(n, ast.ClassDef) and n.name == cls)
        additions = [n for n in target.body if isinstance(n, ast.FunctionDef) and n.name in ('config_file', 'connect')]
        assert len(additions) == 2
        target.body = [n for n in target.body if n not in additions]
        assert ast.dump(original) == ast.dump(stripped), 'Draft changes existing behavior or assertions: ' + filename
        pins[filename] = digest(drafts / filename)
    if args.check_only:
        print('Both drafts preserve every original AST statement and add only config_file/connect overrides.')
        return 0
    assert gate['status'] != 'running', 'Wait for the existing gate'
    out = root / 'local/m04-setup-deadline-correction-01'
    out.mkdir(parents=True, exist_ok=False)
    record = dict(status='running', host=host, candidate=gate['candidate'], draft_sha256=pins,
                  phase_complete=False, gate_sha256=digest(gate_path), results=[], runner_pid=os.getpid())
    def save():
        (out / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    save()
    try:
        for profile in ('Debug', 'ASan-UBSan'):
            row = gate['profiles'][profile]
            build = root / 'local/m04-gate-m04-full-gate-01' / profile
            assert row['binary_sha256']
            for name, expected in row['binary_sha256'].items():
                assert digest(build / name) == expected, name
            inventory = json.loads((gate_path.parent / profile / 'inventory.log').read_text())
            tests = [t for t in inventory['tests'] if t['name'] in specs]
            assert len(tests) == 2
            for test in tests:
                filename, cls, expected_count = specs[test['name']]
                target = out / profile / test['name']
                target.mkdir(parents=True)
                properties = {p['name']: p['value'] for p in test['properties']}
                env = dict(os.environ)
                env.update(item.split('=', 1) for item in properties.get('ENVIRONMENT', []))
                env.update(PYTHONPATH=str(root / 'tests/core') + ':' + str(build / 'python') + ':' + str(root),
                           SPATIAL_SECURITY_EVIDENCE=str(target / 'services'),
                           SPATIAL_SCOPE_EVIDENCE=str(target / 'scopes'))
                original_script = str(root / 'tests/core' / filename)
                assert test['command'].count(original_script) == 1
                command = [str(drafts / filename) if item == original_script else item for item in test['command']]
                started = time.monotonic()
                with (target / 'test.log').open('wb') as log:
                    result = subprocess.run(command, cwd=build, env=env, stdout=log,
                        stderr=subprocess.STDOUT, timeout=properties['TIMEOUT'])
                text = (target / 'test.log').read_text()
                count = re.search(r'Ran (\d+) tests? in', text)
                passed = (result.returncode == 0 and count is not None and int(count[1]) == expected_count
                          and re.search(r'^OK\s*$', text, re.MULTILINE) is not None
                          and not re.search(r'^FAIL:|^ERROR:|ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:', text, re.MULTILINE))
                record['results'].append(dict(profile=profile, suite=test['name'], command=command,
                    exit_code=result.returncode, passed=bool(passed), checks=int(count[1]) if count else None,
                    seconds=time.monotonic()-started, log_sha256=digest(target / 'test.log')))
                save()
                print(profile, test['name'], 'passed' if passed else 'FAILED', flush=True)
        assert all(digest(drafts / name) == expected for name, expected in pins.items())
        assert all(digest(root / name) == expected for name, expected in gate['source_sha256'].items())
        record['status'] = 'passed_needs_primary_review' if all(r['passed'] for r in record['results']) else 'failed'
    except BaseException as error:
        record.update(status='failed', error=repr(error))
        raise
    finally:
        save()
    return 0 if record['status'] == 'passed_needs_primary_review' else 1

if __name__ == '__main__':
    raise SystemExit(main())
