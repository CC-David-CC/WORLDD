"""Audit composed session-resource evidence without running Linux code locally."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
evidence = root / 'evidence/m03-session-resources-2026-10-10'
sha = lambda data: hashlib.sha256(data).hexdigest()
read = lambda path: json.loads(path.read_bytes())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def cases(data):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(data))
        if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')}


manifests = {}; snapshots = {}; archives = {}
for candidate in ('01', '02', '03'):
    path = root / 'local' / f'm03-session-resources-{candidate}-source.tar.gz'
    archives[candidate] = sha(path.read_bytes())
    with tarfile.open(path) as tar:
        prefix = f'm03-session-resources-{candidate}/'
        manifest = json.loads(tar.extractfile(prefix + 'manifest.json').read())
        manifests[candidate] = manifest
        snapshots[candidate] = {name: tar.extractfile(prefix + 'source/' + name).read() for name in manifest['files']}
    assert all(sha(data) == manifest['files'][name] for name, data in snapshots[candidate].items())
    assert all(manifest['files'][name] == value for name, value in manifest['locked_files'].items())
assert manifests['01']['files'] == manifests['02']['files']
assert {name for name in snapshots['03'] if snapshots['01'][name] != snapshots['03'][name]} == {'tests/native/test_session_resources.py'}
assert all(sha((root/name).read_bytes()) == value for name, value in manifests['03']['files'].items())
base_commit = manifests['03']['base_commit']
for name, count in [('tests/core/test_broker_resources.py', 11), ('tests/native/test_session_application.py', 3),
                    ('tests/core/test_process_termination.py', 6), ('tests/native/test_authority_supervisor.py', 14)]:
    prior = cases(subprocess.check_output(['git', 'show', base_commit + ':spatial-os-luna-handoff/' + name], cwd=root))
    current = cases(snapshots['03'][name])
    assert len(prior) == count and all(current[n] == value for n, value in prior.items()), name
native_before = cases(snapshots['01']['tests/native/test_session_resources.py'])
native_after = cases(snapshots['03']['tests/native/test_session_resources.py'])
changed = {name for name in native_after if native_before[name] != native_after[name]}
assert changed == {'test_native_application_has_kernel_budgets_inside_broker_scope'}
for candidate in ('01', '02'):
    deltas = {name: data for name, data in snapshots[candidate].items() if data != snapshots['03'][name]}
    for name, data in deltas.items():
        path = evidence / ('candidate-' + candidate) / 'source-delta' / name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    write(evidence / ('candidate-' + candidate) / 'source-preservation.json', dict(
        basis='Candidate03 manifest. Other source bytes are identical.', archive_sha256=archives[candidate],
        deltas={name: sha(data) for name, data in deltas.items()}))

record = dict(status='primary_review_complete', phase='M03', phase_complete=False,
    base_commit=base_commit, source_archives=archives, source_files=len(manifests['03']['files']),
    locked_files=len(manifests['03']['locked_files']), passed=172, selected_per_host_profile=43,
    failed_final=0, skipped=0, verified_attachments=0, hosts={},
    qualification='Debug: candidate02 unaffected cases plus candidate03 corrected native teardown. Sanitizers: candidate03 all selected cases.',
    review='Primary agent reviewed production code, schema, fixtures, CMake, runners, and raw evidence. No local-model or subagent review.')
logs = []
suite_cases = {
    'broker-resources': set(cases(snapshots['03']['tests/core/test_broker_resources.py'])),
    'process-termination': set(cases(snapshots['03']['tests/core/test_process_termination.py'])),
    'authority-supervisor': set(cases(snapshots['03']['tests/native/test_authority_supervisor.py'])),
    'session-resources': set(native_after) | set(cases(snapshots['03']['tests/native/test_session_application.py']))}
assert sum(map(len, suite_cases.values())) == 43
ok = lambda text: set(re.findall(r'^(test_\w+) \([^\n]+\) \.\.\. ok$', text, re.M))
for host in ('sb16', 'sb16-2'):
    reviews = {}
    for candidate in ('01', '02', '03'):
        directory = evidence / ('candidate-' + candidate) / host
        value = read(directory / 'review.json'); reviews[candidate] = value
        assert 'ended' in value
        assert all(sha((directory/name).read_bytes()) == digest for name, digest in value['attachments'].items())
        assert read(directory/'manifest.json') == manifests[candidate]
        record['verified_attachments'] += len(value['attachments'])
        logs.extend(directory/name for name in value['attachments'] if name.endswith('.log'))
    assert reviews['01']['status'] == reviews['02']['status'] == 'failed'
    assert reviews['03']['status'] == 'passed_review_pending'
    assert reviews['02']['baseline']['status'] == 'expected_unsupported_supervisor_configuration'
    profiles = {}
    for profile in ('debug', 'runtime-sanitize'):
        paths = []; total = 0
        for suite, expected in suite_cases.items():
            candidate = '02' if profile == 'debug' else '03'
            path = evidence / ('candidate-' + candidate) / host / (profile + '-' + suite + '.log')
            passed = ok(path.read_text()); paths.append(str(path.relative_to(evidence)))
            if profile == 'debug' and suite == 'session-resources':
                assert passed == expected - changed
                extra = evidence/'candidate-03'/host/'debug-session-resources.log'
                assert ok(extra.read_text()) == changed
                passed |= ok(extra.read_text()); paths.append(str(extra.relative_to(evidence)))
            assert passed == expected, (host, profile, suite, passed ^ expected)
            total += len(passed)
        assert total == 43
        native = evidence/'candidate-03'/host/profile/'native'
        kernel = read(native/'test_native_application_has_kernel_budgets_inside_broker_scope-kernel.json')
        assert len(kernel) == 3
        assert sorted(int(group['memory.max']) for group in kernel.values()) == [128*2**20, 256*2**20, 512*2**20]
        profiles[profile] = dict(passed=43, failed=0, skipped=0, logs=paths,
            binaries=reviews['03']['profiles'][profile]['binaries'], native_kernel=kernel)
    cleanup = read(evidence/(host+'-cleanup.json'))
    assert cleanup['normal_checkout_status'] == '' and cleanup['ssh_recovery']
    record['hosts'][host] = dict(profiles=profiles, server=reviews['03']['server'],
        unloaded_units=len(cleanup['units']), recovery_sha256=sha((evidence/(host+'-cleanup.json')).read_bytes()))
for path in logs:
    assert not re.search(r'ERROR: AddressSanitizer|ERROR: LeakSanitizer|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:', path.read_text(errors='replace')), path
record.update(sanitizer_logs_scanned=len(logs), sanitizer_matches=0,
    preserved_failures=[
        'Candidate01: both Debug builds completed. Old supervisor rejected broker_resources. An incorrect evidence filename pattern stopped the runner.',
        'Candidate02: 42 selected Debug cases passed per host. New kernel witness omitted world shutdown before broker shutdown. Runtime rejected the invalid order.',
        'Candidate03: the new witness now checks world shutdown first. The corrected Debug case and all 43 sanitizer cases passed per host.'],
    limitations=[
        'M03 and Spatial-M1 remain incomplete. Normal host checkouts are unchanged.',
        'Native evidence uses the modified XLibre software display. Physical devices and GPU performance remain unqualified.',
        'Whole-session deployment and other application fixtures still need explicit controller and resource provisioning.',
        'Storage quotas, broker Git/file accounting, and independent escape qualification remain pending.',
        'These checks do not establish issuer fdatasync latency under competing writes.',
        'Five-second production timeout, short fault fixtures, and SQLite durability remain unchanged.',
        'Debug binaries come from candidate01. Production source is identical in all three candidates.',
        'No full M03 phase gate, power-loss test, or live-model acceptance ran.'],
    next=['Provision remaining production entry points and application fixtures.',
          'Implement bounded persistent storage and broker effect accounting.',
          'Repeat issuer contention measurements, then qualify the full M03 gate.'])
write(evidence/'primary-review.json', record)
print(json.dumps({key:record[key] for key in ('status','passed','verified_attachments','sanitizer_logs_scanned','source_files','locked_files')}))
