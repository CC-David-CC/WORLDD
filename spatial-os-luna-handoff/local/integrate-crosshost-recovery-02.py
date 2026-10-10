"""Import reviewed two-host recovery evidence without changing failed runs."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile

root = Path(__file__).resolve().parents[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'crosshost-recovery-02'; assert not target.exists()
sha = lambda data: hashlib.sha256(data).hexdigest()
read = lambda path: json.loads(path.read_bytes())
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
archives = {'sb16': '36afa89e375bcd42f331a8cc5e7a33b4653faeede6776ff9b8857ff42b6a3dd6',
    'sb16-2': '8696966509d9bdcde9c584f14b66c80c53cffcfb8fe17f2ed0c8699d9515efb4'}
collected = {}
for host, digest in archives.items():
    archive = root / ('local/crosshost-recovery-checks-02-' + host + '.tar.gz')
    assert sha(archive.read_bytes()) == digest
    prefix = 'crosshost-recovery-02/' + host + '/'
    with tarfile.open(archive) as tar:
        members = tar.getmembers(); assert len(members) == len({member.name for member in members})
        for member in members:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            assert member.name.startswith(prefix) and 'private-bundle' not in name.parts
            assert evidence.joinpath(*name.parts).resolve().is_relative_to(evidence.resolve())
        for member in members:
            path = evidence.joinpath(*PurePosixPath(member.name).parts); path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
    value = read(target / host / 'collected-review.json'); collected[host] = value
    assert value['status'] == 'focused_passed_phase_gate_pending' and value['coordinated_passed'] == 8
    assert all(sha((target / host / name).read_bytes()) == expected for name, expected in value['attachments'].items())
    for profile in ('debug', 'runtime-sanitize'):
        name = 'm02-crosshost-cancellation-01/checks/' + profile + '/build-review.json'
        assert sha((target / host / name).read_bytes()) == sha((evidence / 'crosshost-cancellation-03' / host / name).read_bytes())
manifest = read(target / 'sb16/m02-crosshost-recovery-02/manifest.json')
assert manifest == read(target / 'sb16-2/m02-crosshost-recovery-02/manifest.json')
current = {name: sha((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in manifest['files']}
assert current == manifest['files'] and len(current) == 347
assert all(current[name] == value for name, value in manifest['locked_files'].items())
coordination = target / 'coordination'; coordination.mkdir()
cases = []
for tag in ('01', '02'):
    combinations = [('debug', 'crash', 'forward')] if tag == '01' else [
        (profile, scenario, direction) for profile in ('debug', 'runtime-sanitize')
        for scenario in ('crash', 'delay') for direction in ('forward', 'reverse')]
    for profile, scenario, direction in combinations:
        label = profile + '-' + scenario + '-' + direction + '01'
        local = root / ('local/coordinate-recovery-' + tag + '-' + label)
        state = read(local / 'state.json')
        assert state['status'] == ('failed_or_observation_incomplete' if tag == '01' else 'passed')
        assert len(state['results']) == 3 and all(node['exited'] for node in state['nodes'])
        if tag == '02': assert all(node['exit_code'] == 0 for node in state['results'])
        else: assert sorted(node['exit_code'] for node in state['results']) == [0, 1, 1]
        destination = coordination / (tag + '-' + label); destination.mkdir()
        for path in local.iterdir():
            if path.is_file() and (path.suffix == '.log' or path.name == 'state.json'):
                shutil.copyfile(path, destination / path.name)
        if tag == '01': continue
        issuer = 'sb16' if direction == 'forward' else 'sb16-2'
        for host in archives:
            case, = [item for item in collected[host]['cases'] if item['candidate'] == tag and
                item['profile'] == profile and item['scenario'] == scenario and item['direction'] == direction]
            assert all(node['status'] == 'passed' and node['exit_code'] == 0 for node in case['nodes'].values())
            if host == issuer:
                witness = case['nodes']['controller']['witness']
                assert witness['exact_generations'] == 4 and witness['held_bytes'] > 0
                assert witness['retirement_obligations'] == (1 if scenario == 'crash' else 0)
        cases.append(dict(profile=profile, scenario=scenario, direction=direction, issuer=issuer, status='passed', witness=witness))
review = dict(status='reviewed_focused_coverage_phase_gate_pending', reviewer='primary agent',
    archive_sha256=archives, source_files=347, locked_files_unchanged=41, integrated_source=current,
    cases=cases, selected_passed=8, selected_failed=0, selected_skipped=0,
    new_crash_cases=4, delayed_delivery_regressions=4, preserved_failed_candidate='01',
    preserved_failure='After successful retirement, the initial fixture sent another stop_world. The coordinator rejected the already-stopped world. Candidate02 uses the previously checked cleanup receipt during final teardown.',
    collection_correction='The initial collector expected three application resize acknowledgements on the crashed host. The existing crash_world fixture checks twice, then recovery and retirement each check once. Collection now verifies exactly four recorded acknowledgements. Test source and original evidence did not change for this correction.',
    production_runtime_behavior_changed=False, compiled_input_changes=0,
    build_provenance='Both profiles reuse hash-verified crosshost-cancellation-01 binaries. Only the Python test scenario and its test-only protobuf schema changed. New bindings were generated separately on each Linux host.',
    file_review={
        'tests/native/crosshost_revocation.py': 'Adds an actual pidfd-targeted world crash while encrypted revocation bytes remain held. Production coordinator operations perform checked scope cleanup, dependent recovery and signed retirement. Independent native events and durable receipts verify each boundary. Existing delayed delivery remains covered.',
        'tests/proto/crosshost_revocation.proto': 'Adds typed test commands and public cleanup evidence fields without reusing field numbers. No production interface or actor authority changes.',
        'local/coordinate-crosshost-check.ps1': 'Selects finite candidate/scenario names, uses unique output paths, and requires the controller and both workers to exit successfully. Background SSH processes remain hidden and tracked.',
        'local recovery helpers': 'Freeze source, regenerate test bindings, verify unchanged compiled inputs, preserve failed runs, and collect raw evidence without credentials or runtime databases.',
        'docs/CROSS_HOST_RECOVERY_CHECKS.txt': 'Records exact pending, cleanup, recovery and retirement invariants and selected checks before execution.',
        'historical baseline documents': 'Label old source findings as historical and point to current evidence. Requirements remain unchanged.',
        'status and task records': 'Record focused coverage without claiming full M02 completion.'},
    remaining=['Issuer interruption and remaining native recovery failure races.',
        'Both earlier intermittent timeout causes.', 'Full required M02 qualification on both hosts.'],
    limits=['These are two independent worlds sharing an issuer, not shared-world geometry acceptance.',
        'Physical input, GPU sessions, long-duration operation and performance acceptance remain separate.'])
write(target / 'primary-review.json', review)
status_path = root / 'CURRENT_STATE.md'; status = status_path.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert status.startswith(heading)
update = '''
## 2026-10-10: Cross-host world crash recovery passes focused native checks

Eight coordinated scenarios passed in Debug and runtime ASan/UBSan across both hosts.
Four cases crash the delayed world. Four cases check ordinary delayed delivery after the fixture refactor.
The crashed world releases native input, but its issuer obligation remains pending.
Production dependent recovery releases the exact reservation and persists the cleanup receipt.
The issuer obligation remains pending until explicit checked retirement.
Retirement preserves applied revision zero. The other three generations complete through revision-one acknowledgements.
Both applications keep responding. The other host retains its original world and broker launch identities.
Read evidence/control-recovery-2026-10-09/crosshost-recovery-02/primary-review.json.

The first candidate failed during fixture teardown after retirement. It sent a second stop for the stopped world.
Candidate02 uses the checked cleanup receipt. The original failed run remains preserved.
All 347 source hashes match. All 41 locked files remain unchanged.
Production compiled inputs remain unchanged. Both profiles reuse verified binaries and separately generated test bindings.

Next: issuer interruption during native recovery and remaining failure races.
Both earlier intermittent timeout causes remain unresolved. Full M02 qualification remains pending on both hosts.
M02 and Spatial-M1 remain incomplete. No normal desktop session or database changed.

'''
status_path.write_bytes((heading + update + status[len(heading):]).encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = read(task_path)
task['current_step'] = 'issuer_interruption_during_native_recovery'
task['crosshost_crash_recovery'] = dict(state='Eight selected coordinated scenarios passed both profiles and directions.',
    evidence='evidence/control-recovery-2026-10-09/crosshost-recovery-02/primary-review.json',
    selection='docs/CROSS_HOST_RECOVERY_CHECKS.txt', new_crash_cases=4, delayed_delivery_regressions=4,
    hosts=['sb16', 'sb16-2'], full_phase_gate='pending on both hosts', preserved_failed_candidate='01')
task['remaining'] = [value.replace('Qualify remaining cross-host crash and recovery races. Complete full M02 qualification on both hosts.',
    'Qualify issuer interruption and remaining recovery races. Complete full M02 qualification on both hosts.') for value in task['remaining']]
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = read(driver_path)
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail='Cross-host crash recovery and delayed-delivery regressions passed eight scenarios, with native and durable issuer witnesses.')
driver['crosshost_recovery'].update(status='reviewed_focused_coverage', session=None, terminal_session=89595,
    exit_code=0, selected_passed=8, selected_failed=0, preserved_failure_count=1,
    evidence=task['crosshost_crash_recovery']['evidence'], full_phase_gate='pending on both hosts')
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(('\n2026-10-10: Native cross-host crash recovery passed both directions in Debug and runtime sanitizers.\n'
        'Four new crash cases and four delayed-delivery regressions passed.\n'
        'Native cleanup and dependent reservation recovery each leave the crashed world obligation pending.\n'
        'Explicit production retirement completes only that obligation and preserves applied revision zero.\n'
        'The other three generations acknowledge revision one. Both applications keep responding.\n'
        'Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/crosshost-recovery-02/primary-review.json.\n'
        'Candidate01 failed because final fixture teardown stopped an already-retired world. The failure remains preserved.\n'
        'The collector initially expected three resize replies; the existing crash helper emits two, making four total.\n'
        'Collection was corrected against unchanged native logs. No test or runtime change was needed for that count.\n'
        'Historical baseline documents now identify their source findings as historical and link current evidence.\n'
        'Next: issuer interruption during native recovery. Both intermittent causes and the full M02 gate remain pending.\n'
        'M02 and Spatial-M1 remain incomplete. No normal session or database changed.\n').encode())
print(json.dumps(dict(status=review['status'], selected_passed=8, source_files=347,
    attachments=sum(len(value['attachments']) for value in collected.values()))))
