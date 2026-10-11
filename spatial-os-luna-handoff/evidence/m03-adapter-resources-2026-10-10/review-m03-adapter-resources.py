"""Audit frozen Linux execution evidence without executing project code locally."""
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
evidence = root/'evidence/m03-adapter-resources-2026-10-10'
sha = lambda data: hashlib.sha256(data).hexdigest()
read = lambda path: json.loads(path.read_bytes())


def cases(data):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(data))
            if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')}


archive = root/'local/m03-adapter-resources-01-source.tar.gz'
assert sha(archive.read_bytes()) == '82810a05fafa78f5b0eb2fbb1777628da94e678cc47df484d9fdaa11f4ce71b8'
with tarfile.open(archive) as tar:
    prefix = 'm03-adapter-resources-01/'
    manifest = json.loads(tar.extractfile(prefix+'manifest.json').read())
    snapshot = {name: tar.extractfile(prefix+'source/'+name).read() for name in manifest['files']}
assert len(snapshot) == 365 and len(manifest['locked_files']) == 41
assert all(sha(data) == manifest['files'][name] for name, data in snapshot.items())
assert all(sha((root/name).read_bytes()) == value for name, value in manifest['files'].items())
assert all(manifest['files'][name] == value for name, value in manifest['locked_files'].items())
base = manifest['base_commit']
assert base == 'eebfec365b865b23b27a32a30e6b63ed9ace38c7'
suite_sources = {'broker-resources': ('tests/core/test_broker_resources.py', 15),
    'workspace-context': ('tests/core/test_workspaces.py', 13),
    'workspace-processes': ('tests/core/test_processes.py', 18),
    'adapter-resources': ('tests/core/test_adapter_resources.py', 4)}
expected = {}
for suite, (name, count) in suite_sources.items():
    current = cases(snapshot[name]); assert len(current) == count
    expected[suite] = sorted(current)
    if suite != 'adapter-resources':
        previous = subprocess.check_output(['git','show',base+':spatial-os-luna-handoff/'+name], cwd=root)
        assert cases(previous) == current, name
        if suite != 'workspace-context': assert previous == snapshot[name], name

record = dict(status='primary_review_complete', phase='M03', phase_complete=False,
    base_commit=base, source_archive_sha256=sha(archive.read_bytes()), source_files=365, locked_files=41,
    selected_per_host_profile=50, passed=0, failed_final=0, skipped=0, verified_attachments=0,
    unchanged_existing_test_bodies=46, expected_cases=expected, hosts={},
    review='Primary agent reviewed source, protobuf, CMake, fixtures, cleanup ownership, and raw evidence. No local-model or subagent review.',
    context_case_evidence='Workspace-context logs report 13 successful cases without names. Frozen unchanged test bodies and the standard unittest loader identify those cases.')
logs = []
for host in ('sb16','sb16-2'):
    directory = evidence/'candidate-01'/host
    review = read(directory/'review.json')
    assert review['status'] == 'passed_review_pending' and 'ended' in review
    assert read(directory/'manifest.json') == manifest
    assert sha((directory/'manifest.json').read_bytes()) == review['manifest_sha256']
    assert sha((directory/'runner.py').read_bytes()) == review['runner_sha256']
    for name, value in review['attachments'].items():
        path = directory/name
        assert sha(path.read_bytes()) == value, path
        record['verified_attachments'] += 1
        if name.endswith('.log'): logs.append(path)
    for command in review['commands']:
        assert sha((directory/(command['label']+'.log')).read_bytes()) == command['sha256']
        assert command['exit_code'] == (1 if command['label'] == 'baseline' else 0)
    baseline = (directory/'baseline.log').read_text()
    assert review['baseline']['status'] == 'expected_ignored_resource_configuration'
    assert "'manager' != 'broker-manager'" in baseline and 'FAILED (failures=1)' in baseline
    receipts = list((directory/'baseline-witnesses').glob('adapter-provisioning-*.json'))
    assert len(receipts) == 1
    receipt = read(receipts[0])
    assert receipt['scope_removed'] and not receipt['unexpected_population'] and receipt['exit_code'] == 1
    profiles = {}
    for profile in ('debug','runtime-sanitize'):
        result = review['profiles'][profile]
        assert result['passed'] == 50 and result['failed'] == result['skipped'] == 0
        text = (directory/(profile+'-checks.log')).read_text()
        assert [int(n) for n in re.findall(r'Ran (\d+) tests? in ',text)] == [15,13,18,4]
        assert '100% tests passed' in text and 'skipped=' not in text
        for suite, names in expected.items():
            match = re.search(r'    Start \d+: '+re.escape(suite)+r'\n(.*?)(?=\n\d+/4 Test)',text,re.S)
            assert match, suite
            section = match.group(1)
            assert re.search(r'^\d+: OK$',section,re.M)
            if suite != 'workspace-context':
                passed = re.findall(r'^\d+: (test_\w+) \([^\n]+\) \.\.\. ok$',section,re.M)
                assert sorted(passed) == names, (host,profile,suite,passed)
            else:
                assert re.search(r'^\d+: \.{13}$',section,re.M)
        witnesses = directory/profile/'witnesses'
        receipts = list(witnesses.glob('adapter-provisioning-*.json'))
        assert len(receipts) == 3
        for path in receipts:
            receipt = read(path)
            assert receipt['scope_removed'] and not receipt['unexpected_population'] and receipt['exit_code'] == 0
        kernel_path = witnesses/'test_explicit_configuration_reaches_kernel_and_closes_private_descriptor/resource-kernel.json'
        kernel = read(kernel_path)
        manager = PurePosixPath(kernel['manager']); world = PurePosixPath(kernel['world'])
        application = PurePosixPath(kernel['application'])
        assert manager.name == 'broker-manager' and not world.is_relative_to(manager.parent)
        assert application.is_relative_to(manager.parent) and len(application.relative_to(manager.parent).parts) == 3
        limits = {name:value.strip() for name,value in kernel['limits'].items()}
        for name, value in {'memory.max':'134217728','memory.swap.max':'0','pids.max':'16','cpu.max':'100000 100000'}.items():
            assert limits[name] == value
        assert 'rbps=67108864 wbps=16777216' in limits['io.max']
        assert len(limits['cgroup.procs'].split()) >= 3
        assert not any('broker-resource-policy' in link for link in kernel['broker_fds'])
        profiles[profile] = dict(passed=50,failed=0,skipped=0,binaries=result['binaries'],
            kernel_evidence=str(kernel_path.relative_to(evidence)), provisioned_suites=3)
        record['passed'] += 50
    cleanup = read(evidence/(host+'-cleanup.json'))
    assert len(cleanup['units']) == 9 and cleanup['ssh_recovery']
    assert cleanup['normal_checkout_head'] == 'b5902eaaad0a9d0ce3cb272d37f0d0936a6575cd'
    assert cleanup['normal_checkout_status'] == ''
    for name in ('root_subtree_control','user_controllers'):
        assert set(cleanup[name].split()) == {'cpu','memory','pids'}
    assert all(item['exit_code'] == 0 for item in cleanup['commands'])
    assert all('LoadState=not-found' in item['stdout'] and 'ActiveState=inactive' in item['stdout']
        for item in cleanup['commands'] if item['argv'][0] == 'systemctl')
    record['hosts'][host] = dict(profiles=profiles,baseline=review['baseline'],unloaded_units=9,
        cleanup_sha256=sha((evidence/(host+'-cleanup.json')).read_bytes()))
for path in logs:
    assert not re.search(r'ERROR: AddressSanitizer|ERROR: LeakSanitizer|SUMMARY: (?:AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:',path.read_text(errors='replace')),path
assert record['passed'] == 200
record.update(sanitizer_logs_scanned=len(logs),sanitizer_matches=0,
    preserved_failures=['Both old adapters ignored explicit resource configuration. The independent kernel check observed manager instead of broker-manager.'],
    limitations=[
        'M03 and Spatial-M1 remain incomplete. This is focused qualification, not the phase gate.',
        'Non-native adapter integration uses explicit test-service provisioning. Operator deployment and other direct-broker/authenticated fixtures remain pending.',
        'Abrupt adapter death cannot run its destructor. The owning delegated service reclaims only its empty residual groups; surviving processes fail qualification.',
        'Storage quotas and broker Git/file effect accounting remain pending.',
        'This run does not establish issuer fdatasync latency under competing writes. Five-second deadlines and database durability remain unchanged.',
        'Normal host checkouts are unchanged. No operator runtime was restarted or migrated.',
        'No new full gate, native GPU, physical input, live model, or power-loss qualification ran.'],
    next=['Provision remaining authenticated/direct-broker fixtures and operator entry points.',
          'Implement bounded persistent storage and broker effect accounting.',
          'Repeat issuer pressure and durable-acknowledgement measurements, then run the full M03 gate.'])
(evidence/'primary-review.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
for name in ('review-m03-adapter-resources.py','collect-m03-adapter-resources.py','check-m03-adapter-cleanup.py'):
    (evidence/name).write_bytes((root/'local'/name).read_bytes())
print(json.dumps({key:record[key] for key in ('status','passed','verified_attachments','sanitizer_logs_scanned','source_files','locked_files')}))
