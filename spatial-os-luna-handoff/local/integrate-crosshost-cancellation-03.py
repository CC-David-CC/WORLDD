"""Import byte-pinned Linux evidence and record the reviewed development checkpoint."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile

root = Path(__file__).resolve().parents[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'crosshost-cancellation-03'
assert not target.exists()
sha = lambda value: hashlib.sha256(value).hexdigest()
read = lambda path: json.loads(path.read_bytes())
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
archives = {'sb16': 'b813f52ddefa5244d6c5503089502eb481c3361a145882acfda646d2898fb170',
    'sb16-2': 'e4bb79eff6f3cf0ac774668344b208af5ba45e6597cc96e3586e326fd25a8b4e'}
collected = {}
for host, digest in archives.items():
    archive = root / ('local/crosshost-cancellation-checks-03-' + host + '.tar.gz')
    assert sha(archive.read_bytes()) == digest
    prefix = 'crosshost-cancellation-03/' + host + '/'
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert len(members) == len({item.name for item in members})
        for item in members:
            name = PurePosixPath(item.name)
            assert item.isfile() and not name.is_absolute() and '..' not in name.parts
            assert item.name.startswith(prefix) and 'private-bundle' not in name.parts
            assert evidence.joinpath(*name.parts).resolve().is_relative_to(evidence.resolve())
        for item in members:
            path = evidence.joinpath(*PurePosixPath(item.name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream: stream.write(tar.extractfile(item).read())
    value = read(target / host / 'collected-review.json'); collected[host] = value
    assert value['host'] == host and value['status'] == 'focused_passed_phase_gate_pending'
    assert all(sha((target / host / name).read_bytes()) == expected for name, expected in value['attachments'].items())
    for profile in ('debug', 'runtime-sanitize'):
        assert value['related'][profile] == {'authority-owned-participant': 11, 'session-application': 3}

manifest = read(target / 'sb16/m02-crosshost-cancellation-03/manifest.json')
assert manifest == read(target / 'sb16-2/m02-crosshost-cancellation-03/manifest.json')
current = {name: sha((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in manifest['files']}
assert current == manifest['files'] and len(current) == 347
assert all(current[name] == value for name, value in manifest['locked_files'].items())
coordination = target / 'coordination'; coordination.mkdir()
final_cases = []
for profile in ('debug', 'runtime-sanitize'):
    for direction in ('forward', 'reverse'):
        issuer = 'sb16' if direction == 'forward' else 'sb16-2'
        for host in archives:
            case, = [item for item in collected[host]['cases'] if item['tag'] == '03' and item['profile'] == profile and item['direction'] == direction]
            assert all(node['status'] == 'passed' and node['exit_code'] == 0 for node in case['nodes'].values())
            assert case['nodes']['worker']['witness']['native_press_and_release']
            if host == issuer:
                witness = case['nodes']['controller']['witness']
                assert witness['exact_generations'] == 4 and witness['held_encrypted_bytes'] > 0
                assert not witness['retirement_used']
        local = root / ('local/coordinate-03-' + profile + '-' + direction + '01')
        state = read(local / 'state.json')
        assert state['status'] == 'passed' and len(state['results']) == 3
        assert all(node['exit_code'] == 0 for node in state['results'])
        destination = coordination / (profile + '-' + direction); destination.mkdir()
        for path in local.iterdir():
            if path.is_file() and (path.suffix == '.log' or path.name == 'state.json'):
                shutil.copyfile(path, destination / path.name)
        final_cases.append(dict(profile=profile, direction=direction, issuer=issuer,
            workers=['sb16', 'sb16-2'], status='passed', witness=witness))

review = dict(status='reviewed_focused_coverage_phase_gate_pending', reviewer='primary agent',
    archive_sha256=archives, source_files=347, locked_files_unchanged=41, integrated_source=current,
    final_crosshost_cases=final_cases, selected_crosshost_passed=4, selected_crosshost_failed=0,
    related_cases_per_host_per_profile=14, related_cases_total=56, related_failed=0, related_skipped=0,
    prior_exploratory_debug_passed=2, preserved_failed_crosshost_cases=1,
    preserved_failure='Candidate01 omitted world teardown before session broker teardown. The production coordinator rejected that order. Candidate02 corrected only fixture ordering. The original failed evidence remains intact.',
    production_runtime_behavior_changed=False,
    build_reuse='Candidate02 and candidate03 differ from candidate01 only in the new Python scenario. All compiled inputs and binaries match the recorded candidate01 builds.',
    file_review={
        'tests/native/test_authority_owned_participant.py': 'The default fixture is unchanged. A hook supplies the genuine remote issuer. Launch binding and runtime config use the actual registry revision instead of assuming one.',
        'tests/native/test_session_application.py': 'Enrollment and activation-check hooks preserve the original assertions by default. The cross-host controller checks the four real activation rows. Native confinement and application witnesses remain shared with the existing suite.',
        'tests/proto/crosshost_revocation.proto': 'Versioned test coordination uses typed requests and replies. Private keys are external disposable fixture files. Launch records contain identities and proofs, never private keys or native cookies.',
        'tests/native/crosshost_revocation.py': 'One real issuer serves real sessions on both hosts. An opaque relay holds encrypted reply bytes. Native releases, surviving applications, invalidated actor access, and exact issuer obligations are checked. Cleanup stops each world before its broker.',
        'CMakeLists.txt': 'Adds generated Python bindings for the test coordination schema. No production interface schema changes.',
        'docs/CROSS_HOST_CANCELLATION_CHECKS.txt': 'States the invariant and focused selection. The new network fixture uses the production 5000 ms transport deadline. Existing 700 ms tests are unchanged.',
        'local build, coordinator, collection and integration helpers': 'Pin source, binaries, server, commands and evidence. Run Linux workloads only on sb16 and sb16-2. Preserve failed candidates. Exclude private bundles and runtime databases.',
        'status and task records': 'Keep focused success separate from unresolved intermittent failures and full phase qualification.'},
    remaining=['Investigate the two preserved intermittent timeout causes.',
        'Qualify remaining cross-host crash and recovery races.', 'Run full required M02 qualification on both hosts.'],
    limits=['Two independent worlds share an issuer. Shared-world geometry is not qualified here.',
        'Credential revocation preserves independent execution authority. Whole-actor revocation is a separate scenario.',
        'Native GPU, physical input, long-duration and performance acceptance are not established by these disposable Xvfb checks.'])
write(target / 'primary-review.json', review)

status_path = root / 'CURRENT_STATE.md'; status = status_path.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert status.startswith(heading)
update = '''
## 2026-10-10: Cross-host credential cancellation passes focused native checks

Both issuer directions passed in Debug and runtime ASan/UBSan. All four coordinated scenarios passed.
Each scenario uses one real issuer and a native production session on each host.
The issuer tracks four exact world and broker generations across two independent worlds.
One world's encrypted replies remain held while the other host releases its native input and control reservation.
Saved replies prove nonzero held bytes and unreleased input on the delayed host before relay release.
Both applications remain responsive. Revoked actor requests fail. All four obligations complete through acknowledgements.
The two affected existing suites passed 14 cases per host per profile: 56 cases total, without failures or skips.
Read evidence/control-recovery-2026-10-09/crosshost-cancellation-03/primary-review.json.

Candidate01 failed at fixture teardown. The fixture tried to stop its broker before its world.
Candidate02 corrected that order. The original failure and two exploratory Debug successes remain preserved.
Candidate03 strengthens the encrypted-delay witness. Compiled inputs remain identical across all three candidates.
All 347 source hashes match. All 41 locked files remain unchanged. No production runtime behavior changed.

These checks qualify delayed credential cancellation. Shared-world geometry and remaining failure races still need qualification.
The two earlier intermittent timeout causes remain unresolved. The complete both-host M02 phase gate remains pending.
M02 and Spatial-M1 remain incomplete. No normal desktop session or database changed.

'''
status_path.write_bytes((heading + update + status[len(heading):]).encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = read(task_path)
task['current_step'] = 'remaining_recovery_races_and_preserved_timeout_causes'
task['crosshost_cancellation'] = dict(state='Delayed credential cancellation passed both issuer directions and both profiles.',
    evidence='evidence/control-recovery-2026-10-09/crosshost-cancellation-03/primary-review.json',
    selection='docs/CROSS_HOST_CANCELLATION_CHECKS.txt', coordinated_cases_passed=4,
    related_cases_per_host_per_profile=14, hosts=['sb16', 'sb16-2'], full_phase_gate='pending on both hosts',
    preserved_failed_candidate='01', shared_world_geometry_qualified=False)
task['remaining'] = [value.replace('Complete cross-host cancellation and full M02 qualification on both hosts.',
    'Qualify remaining cross-host crash and recovery races. Complete full M02 qualification on both hosts.') for value in task['remaining']]
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = read(driver_path)
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail='Both issuer directions passed Debug and runtime sanitizers with actual native and issuer witnesses. Affected fixture suites also passed on both hosts.')
driver['crosshost_cancellation'].update(status='reviewed_focused_coverage', live_session=None,
    final_crosshost_passed=4, related_cases_total=56, preserved_failure_count=1,
    evidence=task['crosshost_cancellation']['evidence'], full_phase_gate='pending on both hosts')
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(('\n2026-10-10: Cross-host credential cancellation passed both directions in Debug and runtime sanitizers.\n'
        'Four coordinated scenarios passed. Related fixture suites passed 14 cases per host per profile, 56 total.\n'
        'One issuer tracks four exact world and broker generations. Each host runs its own native session and confined application.\n'
        'The delayed host retains input while encrypted replies wait. After release, native input and reservations clear.\n'
        'Both applications survive. Revoked actor requests fail. All four obligations complete by acknowledgement, without retirement.\n'
        'Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/crosshost-cancellation-03/primary-review.json.\n'
        'Candidate01 failed because fixture teardown stopped the broker before the world. That failure remains preserved.\n'
        'Candidate02 fixes fixture ordering. Candidate03 adds a held-byte witness. Production runtime behavior did not change.\n'
        'The two previous intermittent timeout causes remain unresolved. Cross-host crash races and full M02 qualification remain pending.\n'
        'These disposable native checks do not establish shared-world geometry, physical input, native GPU or performance acceptance.\n'
        'M02 and Spatial-M1 remain incomplete. No normal session or database changed.\n').encode())
print(json.dumps(dict(status=review['status'], coordinated_cases=4, related_cases=56,
    source_files=len(current), attachments=sum(len(value['attachments']) for value in collected.values()))))
