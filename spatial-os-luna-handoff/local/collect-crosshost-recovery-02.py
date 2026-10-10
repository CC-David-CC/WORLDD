"""Validate native recovery witnesses and collect terminal, credential-free evidence."""
import hashlib
import json
from pathlib import Path
import socket
import sys
import tarfile

host = socket.gethostname().split('.')[0]; assert host in ('sb16', 'sb16-2')
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-crosshost-recovery-02'; failed = base / 'm02-crosshost-recovery-01'
prior = base / 'm02-crosshost-cancellation-01'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
read = lambda path: json.loads(path.read_bytes())
attachments = {}

def add(path):
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base.resolve())
    assert 'private-bundle' not in path.parts and path.suffix not in ('.pem', '.crt', '.db', '.textproto')
    data = path.read_bytes()
    assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
    attachments[path.relative_to(base).as_posix()] = sha(path)

for candidate in (root, failed):
    manifest = read(candidate / 'manifest.json')
    assert len(manifest['files']) == 347 and len(manifest['locked_files']) == 41
    assert all(sha(candidate / 'source' / name) == value for name, value in manifest['files'].items())
    assert all(manifest['files'][name] == value for name, value in manifest['locked_files'].items())
    binding = read(candidate / 'bindings-review.json'); assert binding['exit_code'] == 0
    assert binding['schema_sha256'] == manifest['files']['tests/proto/crosshost_revocation.proto']
    assert sha(candidate / 'python/crosshost_revocation_pb2.py') == binding['binding_sha256']
    for name in ('manifest.json', 'bindings-review.json', 'bindings.log',
        'source/tests/native/crosshost_revocation.py', 'source/tests/proto/crosshost_revocation.proto'): add(candidate / name)
manifest = read(root / 'manifest.json')
old = read(failed / 'manifest.json')
assert {name for name in manifest['files'] if manifest['files'][name] != old['files'][name]} == {'tests/native/crosshost_revocation.py'}
builds = {}
for profile in ('debug', 'runtime-sanitize'):
    path = prior / 'checks' / profile / 'build-review.json'; build = prior / profile / 'build'
    value = read(path); builds[profile] = value
    assert value['status'] == 'built_tests_unrun' and value['host'] == host
    assert set(value['source']) == set(manifest['files'])
    assert {name for name in value['source'] if value['source'][name] != manifest['files'][name]} == {
        'tests/native/crosshost_revocation.py', 'tests/proto/crosshost_revocation.proto'}
    assert all(sha(prior / 'source' / name) == digest for name, digest in value['source'].items())
    assert all(sha(build / name) == digest for name, digest in value['binaries'].items())
    assert sha(Path(value['server']['path'])) == value['server']['sha256']
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in (build / 'CMakeCache.txt').read_text()
    add(path)
sys.path[:0] = [str(root / 'python'), str(prior / 'debug/build/python')]
import crosshost_revocation_pb2 as wire
import issuer_pb2 as identity
import launch_pb2 as launch

def replies(directory, pattern):
    values = [wire.CrossHostReply.FromString(path.read_bytes()) for path in directory.glob(pattern)]
    assert values and all(value.protocol_version == 1 and value.HasField('state') for value in values)
    result = {value.request_id: value.state for value in values}; assert len(result) == len(values)
    return result

def native(directory, scenario, delayed):
    events = [json.loads(line) for line in (directory / 'native/runTest-probe.jsonl.log').read_text().splitlines()]
    for kind in ('key', 'button'):
        down = next(i for i, row in enumerate(events) if row.get('event') == kind and row.get('pressed') is True)
        up = next(i for i, row in enumerate(events) if row.get('event') == kind and row.get('pressed') is False)
        assert up > down
    # crash_world checks application response both before and after checked scope cleanup.
    expected_ack = 4 if scenario == 'crash' and host == delayed else 2 if scenario == 'crash' else 1
    assert {row.get('seq') for row in events if row.get('event') == 'ack'} == set(range(1, expected_ack+1))
    assert any(row.get('event') == 'configure' and row.get('width') == 640 and row.get('height') == 480 for row in events)
    confinement = read(directory / 'native/runTest-confinement.json.log')
    assert confinement['cwd'] == '/project' and confinement['fds'] == []
    assert confinement['no_new_privs'] and not confinement['host_file_visible']
    states = replies(directory, 'reply-*.pb')
    assert all(state.host == host for state in states.values())
    assert any(state.reservation_released and state.old_actor_rejected and state.application_responded_after_cancellation for state in states.values())
    if scenario == 'crash' and host == delayed:
        data = (directory / 'dependent-cleanup.pb').read_bytes()
        receipt = launch.AuthorityDependentCleanup.FromString(data)
        assert receipt.schema_version == 1 and len(receipt.controls.released) == 1
        assert receipt == launch.AuthorityDependentCleanup(world=states[7].world_cleanup,
            broker=states[7].broker_launch, controls=states[7].control_state.recovery, schema_version=1)
        assert hashlib.sha256(data).digest() == states[7].dependent_cleanup_sha256 == states[8].retirement.request.cleanup_sha256
    return dict(native_input_released=True, resize_acknowledgements=expected_ack, confinement_verified=True)

def controller(directory, scenario, delayed):
    states = replies(directory, '*-reply-*.pb')
    assert set(states) == set(range(1, (11 if scenario == 'crash' else 9)+1))
    starts = {states[i].host: states[i] for i in (1, 2)}
    assert set(starts) == {'sb16', 'sb16-2'}
    expected = {}
    for state in starts.values():
        assert state.native_key_press and state.native_button_press and not state.native_key_release and not state.native_button_release
        for record in (state.world_launch, state.broker_launch):
            assert record.binding.participant_id not in expected
            expected[record.binding.participant_id] = (record.authorization.request.previous_generation + 1, record.binding.service_incarnation)
    assert len(expected) == 4
    held = states[5]
    assert held.host == delayed and held.feed_held and held.held_bytes > 0
    assert not held.native_key_release and not held.native_button_release
    target = starts[delayed].world_launch.binding.participant_id
    labels = ('pending', 'crashed', 'recovered', 'complete') if scenario == 'crash' else ('pending', 'complete')
    barriers = {}
    for label in labels:
        value = identity.AuthorityBarrierStatus.FromString((directory / (label + '-barrier.pb')).read_bytes()); barriers[label] = value
        assert value.revision == 1 and value.participant_count == 4 and len(value.obligations) == 4
        assert {item.registration.participant.id: (item.generation, item.service_incarnation) for item in value.obligations} == expected
        assert value.issuer_id == starts[delayed].world_launch.binding.issuer_id
        obligation, = [item for item in value.obligations if item.registration.participant.id == target]
        if label != 'complete':
            assert not value.complete and value.pending_count > 0
            assert not obligation.complete and obligation.applied_revision == 0 and not obligation.HasField('retirement')
        else:
            assert value.complete and value.pending_count == 0
            for item in value.obligations:
                assert item.complete
                if scenario == 'crash' and item.registration.participant.id == target:
                    assert item.applied_revision == 0 and item.retirement == states[8].retirement
                else: assert item.applied_revision == 1 and not item.HasField('retirement')
    assert all(value.target == barriers['pending'].target and value.target.kind == identity.CREDENTIAL_TARGET for value in barriers.values())
    cancelled = [state for state in states.values() if state.reservation_released]
    assert {state.host for state in cancelled} == {'sb16', 'sb16-2'}
    for state in cancelled:
        assert state.application == starts[state.host].application
        assert state.old_actor_rejected and state.application_responded_after_cancellation and state.native_key_release and state.native_button_release
    if scenario == 'crash':
        crashed, recovered, retired = (states[i] for i in (6, 7, 8))
        assert crashed.premature_retirement_rejected and crashed.control_state.channel_fenced
        assert len(crashed.control_state.reservations) == 1
        assert not recovered.control_state.reservations and recovered.control_state.channel_fenced
        assert list(recovered.control_state.recovery.released) == list(crashed.control_state.reservations)
        cleanup = crashed.world_cleanup
        assert cleanup.stage == launch.OWNED_SCOPES_STOPPED and cleanup.HasField('fence')
        assert cleanup.process_stopped.identity == cleanup.process == starts[delayed].world_launch.process
        assert cleanup.native_stopped.identity == cleanup.native == starts[delayed].world_launch.native
        assert cleanup == recovered.world_cleanup == retired.world_cleanup
        assert retired.retirement.request.owner == crashed.control_state.owner.launch
        assert retired.retirement.request.fence_id == cleanup.fence.fence_id
        assert retired.retirement.request.cleanup_sha256 == recovered.dependent_cleanup_sha256
        assert retired.retirement.applied_revision == 0
        other = starts[states[9].host]; assert other.host != delayed
        assert states[9].world_launch == other.world_launch and states[9].broker_launch == other.broker_launch
    return dict(exact_generations=4, held_bytes=held.held_bytes,
        retirement_obligations=1 if scenario == 'crash' else 0, acknowledged_obligations=3 if scenario == 'crash' else 4,
        retained_pending_through_dependent_recovery=scenario == 'crash', both_applications_survived=True)

cases = []
for candidate in (failed, root):
    is_failure = candidate == failed
    combinations = [('debug', 'crash', 'forward')] if is_failure else [
        (profile, scenario, direction) for profile in ('debug', 'runtime-sanitize')
        for scenario in ('delay', 'crash') for direction in ('forward', 'reverse')]
    for profile, scenario, direction in combinations:
        issuer = 'sb16' if direction == 'forward' else 'sb16-2'; delayed = 'sb16-2' if direction == 'forward' else 'sb16'
        run = candidate / 'runs' / (profile + '-' + scenario + '-' + direction + '01')
        case = dict(candidate='01' if is_failure else '02', profile=profile, scenario=scenario, direction=direction, nodes={})
        for mode in (('controller', 'worker') if host == issuer else ('worker',)):
            path = run / (mode + '-review.json'); review = read(path)
            expected_exit = 1 if is_failure and (host == delayed or mode == 'controller') else 0
            assert review['exit_code'] == expected_exit and review['status'] == ('failed' if expected_exit else 'passed')
            assert review['host'] == host and review['profile'] == profile and review['scenario'] == scenario and review['mode'] == mode
            assert review['source_manifest_sha256'] == sha(candidate / 'manifest.json')
            assert review['bindings_review_sha256'] == sha(candidate / 'bindings-review.json')
            assert review['build_review_sha256'] == sha(prior / 'checks' / profile / 'build-review.json')
            assert review['log_sha256'] == sha(run / (mode + '.log'))
            add(path); add(run / (mode + '.log'))
            directory = run / mode; result = read(directory / 'result.json')
            assert result['status'] == ('failed' if expected_exit else 'passed')
            node = dict(exit_code=expected_exit, status=result['status']); case['nodes'][mode] = node
            if not is_failure:
                node['witness'] = native(directory, scenario, delayed) if mode == 'worker' else controller(directory, scenario, delayed)
            for path in directory.rglob('*'):
                if not path.is_file() or 'private-bundle' in path.parts: continue
                assert path.suffix in ('.log', '.json', '.pb'), path
                if not is_failure and path.suffix == '.log':
                    data = path.read_bytes()
                    assert not any(marker in data for marker in (b'ERROR: AddressSanitizer', b'ERROR: LeakSanitizer',
                        b'AddressSanitizer:DEADLYSIGNAL', b'runtime error:', b'Traceback (most recent call last)', b'FAILED (')), path
                add(path)
        if is_failure:
            add(run / 'launcher-used.py')
            if host == delayed: assert 'Session world already stopped' in (run / 'worker.log').read_text()
        cases.append(case)
summary = dict(status='focused_passed_phase_gate_pending', host=host, source_files=347, locked_files=41,
    coordinated_passed=8, preserved_failure=1, cases=cases, attachments=attachments,
    limitations=['Issuer restart and other recovery interruptions remain pending.',
        'Both earlier intermittent timeout causes remain unresolved.', 'Full M02 qualification remains pending on both hosts.'])
output = base / ('crosshost-recovery-collected-02-' + host + '.json')
with output.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = base / ('crosshost-recovery-checks-02-' + host + '.tar.gz'); prefix = 'crosshost-recovery-02/' + host
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(base / name, arcname=prefix + '/' + name, recursive=False)
    tar.add(output, arcname=prefix + '/collected-review.json', recursive=False)
print(json.dumps(dict(host=host, archive=str(archive), sha256=sha(archive), attachments=len(attachments), coordinated_passed=8)))
