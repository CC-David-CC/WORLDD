"""Collect completed world qualification portions without changing the live run."""
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tarfile

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
mode = sys.argv[1]
assert mode in ('debug-focused', 'focused', 'debug', 'final')
full = mode in ('debug', 'final')
profiles = ['debug', 'runtime-sanitize'] if mode in ('focused', 'final') else ['debug']
root = r / 'local/m02-supervisor-world-handoff-02'
source = root / 'source'
manifest_path = r / 'local/supervisor-world-handoff-candidate-02.json'
manifest = json.loads(manifest_path.read_text())['files']
locked = json.loads((source / 'acceptance.lock.json').read_text())['files']
assert len(manifest) == 290 and len(locked) == 41
for files in (manifest, locked):
    for name, digest in files.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
schema = json.loads((root / 'schema-audit.json').read_text())
assert schema['existing_files'] == 12 and schema['existing_descriptors_unchanged'] and schema['field_number'] == 12
prefix = 'candidate-02/' + host + '/'
files = [(manifest_path, prefix + 'source-manifest.json')]
for name in ('schema-audit.json', 'before.pb', 'after.pb'):
    files.append((root / name, prefix + name))
summary = dict(host=host, candidate=2, mode=mode, source_files=290, locked_files=41,
               integrated=False, schema=schema)
for profile in profiles:
    out = root / profile
    commands = ['configure', 'build', 'harness', 'handoff', 'regressions', 'focused']
    if full:
        commands += ['core', 'P10']
    run_name = 'run-full.json' if full else 'run.json'
    run = json.loads((out / run_name).read_text())
    assert run['host'] == host and run['source_files'] == 290
    assert [v['name'] for v in run['results']] == commands
    assert all(v['exit_code'] == 0 for v in run['results'])
    server = run['server']
    assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest() == server['sha256']
    parts = [('handoff', 1, 1), ('regressions', 1, 13), ('focused', 11, 112)]
    if full:
        parts.append(('core', 82, 1281))
    names = []
    counts_by_part = {}
    total = 0
    for name, suites, count in parts:
        log = (out / (name + '.log')).read_text()
        counts = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', log, re.M)))
        executed = re.findall(r'^\s*Start\s+\d+:\s+(\S+)', log, re.M)
        assert len(counts) == len(executed) == suites and sum(counts) == count
        assert f'100% tests passed, 0 tests failed out of {suites}' in log
        assert '... skipped' not in log and 'skipped=' not in log
        assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL', log)
        names.extend(executed)
        counts_by_part[name] = dict(zip(executed, counts))
        total += count
    assert len(names) == len(set(names)) == (95 if full else 13)
    assert total == (1407 if full else 126)
    harness = (out / 'harness.log').read_text()
    assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
    assert 'skipped=' not in harness and '... skipped' not in harness
    if full:
        listed = json.loads(subprocess.check_output(['ctest', '--test-dir', str(out / 'build'), '--show-only=json-v1']))
        assert {t['name'] for t in listed['tests']} == set(names)
        report = json.loads((out / 'P10.json').read_text())
        assert report['status'] == 'passed' and report['executed'] == len(report['tests']) == 151
        assert all(t['status'] == 'passed' for t in report['tests'])
    binaries = {n: hashlib.sha256((out / 'build' / n).read_bytes()).hexdigest() for n in (
        'worldd', 'workspaced', 'identityd', 'spatialwm', 'spatial-app-init',
        'spatial-test-host', 'spatial-authority-supervisor', 'spatial-authority-launch-probe',
        'spatial-security-auth', 'spatial-issuer-client', 'spatial-xprobe')}
    summary[profile] = dict(core_checks=total, core_suites=len(names), parts=counts_by_part,
        harness_checks=51, P10_cases=151 if full else 'uncollected', failed=0, skipped=0, binaries=binaries)
    label = profile if full else profile + '-focused'
    for name in [run_name] + [n + '.log' for n in commands] + (['P10.json'] if full else []):
        files.append((out / name, prefix + label + '/' + name))
target = r / ('local/world-handoff-summary-' + mode + '-' + host + '-02.json')
target.write_text(json.dumps(summary, indent=2) + '\n')
files.append((target, prefix + 'summary-' + mode + '.json'))
with tarfile.open(r / ('local/world-handoff-' + mode + '-' + host + '-02.tar.gz'), 'w:gz') as archive:
    for path, name in files:
        archive.add(path, arcname=name)
print(host, mode, 'verified290 source files,41 locks,', total, 'core checks per collected profile')
