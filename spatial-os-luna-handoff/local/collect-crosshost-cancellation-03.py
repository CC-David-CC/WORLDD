"""Collect terminal native cross-host evidence without fixture credentials."""
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import tarfile

host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
roots = {tag: base / ('m02-crosshost-cancellation-' + tag) for tag in ('01', '02', '03')}
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
read = lambda path: json.loads(path.read_bytes())
attachments = {}

def add(path):
    assert path.is_file() and not path.is_symlink()
    assert path.resolve().is_relative_to(base.resolve())
    assert 'private-bundle' not in path.parts
    assert path.suffix not in ('.pem', '.crt', '.db', '.textproto')
    data = path.read_bytes()
    assert b'PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
    attachments[path.relative_to(base).as_posix()] = sha(path)

manifests = {}
for tag, root in roots.items():
    manifest = read(root / 'manifest.json'); manifests[tag] = manifest
    assert len(manifest['files']) == 347 and len(manifest['locked_files']) == 41
    assert all(sha(root / 'source' / name) == value for name, value in manifest['files'].items())
    assert manifest['locked_files'] == manifests['01']['locked_files']
    assert all(manifest['files'][name] == value for name, value in manifest['locked_files'].items())
    if tag != '01':
        assert {name for name in manifest['files'] if manifest['files'][name] != manifests['01']['files'][name]} == {'tests/native/crosshost_revocation.py'}
    add(root / 'manifest.json')
    add(root / 'source/tests/native/crosshost_revocation.py')

builds = {}
for profile in ('debug', 'runtime-sanitize'):
    root = roots['01']; checks = root / 'checks' / profile; build = root / profile / 'build'
    review = read(checks / 'build-review.json'); builds[profile] = review
    assert review['status'] == 'built_tests_unrun' and review['source'] == manifests['01']['files']
    assert review['host'] == host and review['profile'] == profile
    assert all(sha(build / name) == value for name, value in review['binaries'].items())
    assert sha(Path(review['server']['path'])) == review['server']['sha256']
    cache = (build / 'CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
    for item in review['commands']:
        path = checks / (item['label'] + '.log')
        assert item['exit_code'] == 0 and sha(path) == item['log_sha256']; add(path)
    add(checks / 'build-review.json')

sys.path.insert(0, str(roots['01'] / 'debug/build/python'))
import crosshost_revocation_pb2 as wire
import issuer_pb2 as identity

def clean_logs(directory):
    for path in directory.rglob('*.log'):
        data = path.read_bytes()
        assert not any(marker in data for marker in (b'ERROR: AddressSanitizer', b'ERROR: LeakSanitizer',
            b'AddressSanitizer:DEADLYSIGNAL', b'runtime error:', b'Traceback (most recent call last)', b'FAILED (')),(path, data[-1000:])

def native_worker(directory):
    native = directory / 'native'
    rows = [json.loads(line) for line in (native / 'runTest-probe.jsonl.log').read_text().splitlines()]
    for event in ('key', 'button'):
        pressed = next(i for i, row in enumerate(rows) if row.get('event') == event and row.get('pressed') is True)
        released = next(i for i, row in enumerate(rows) if row.get('event') == event and row.get('pressed') is False)
        assert released > pressed
    assert any(row.get('event') == 'ack' and row.get('seq') == 1 for row in rows)
    assert any(row.get('event') == 'configure' and row.get('width') == 640 and row.get('height') == 480 for row in rows)
    confinement = read(native / 'runTest-confinement.json.log')
    assert confinement['cwd'] == '/project' and confinement['fds'] == []
    assert confinement['no_new_privs'] and not confinement['host_file_visible']
    assert (native / 'runTest-server-0.log').is_file() and (native / 'runTest-coordinator.log').is_file()
    replies = [wire.CrossHostReply.FromString(path.read_bytes()) for path in directory.glob('reply-*.pb')]
    assert replies and all(reply.protocol_version == 1 and reply.HasField('state') and reply.state.host == host for reply in replies)
    assert any(reply.state.reservation_released and reply.state.old_actor_rejected and reply.state.application_responded_after_cancellation for reply in replies)
    return dict(native_press_and_release=True, application_resize_acknowledged=True, confinement_verified=True)

def issuer_witness(directory, tag, delayed):
    replies = [wire.CrossHostReply.FromString(path.read_bytes()) for path in directory.glob('*-reply-*.pb')]
    by_id = {reply.request_id: reply for reply in replies}
    assert len(by_id) == len(replies) and len(replies) == (9 if tag == '03' else 8)
    assert all(reply.protocol_version == 1 and reply.HasField('state') for reply in replies)
    starts = [by_id[i].state for i in (1, 2)]
    assert {state.host for state in starts} == {'sb16', 'sb16-2'}
    assert len({state.world for state in starts}) == len({state.workspace for state in starts}) == 2
    expected = {}
    for state in starts:
        assert state.native_key_press and state.native_button_press
        assert not state.native_key_release and not state.native_button_release
        for record in (state.world_launch, state.broker_launch):
            key = record.binding.participant_id
            assert key not in expected
            expected[key] = (record.authorization.request.previous_generation + 1, record.binding.service_incarnation)
    pending = identity.AuthorityBarrierStatus.FromString((directory / 'pending-barrier.pb').read_bytes())
    complete = identity.AuthorityBarrierStatus.FromString((directory / 'complete-barrier.pb').read_bytes())
    assert pending.participant_count == 4 and pending.pending_count > 0 and not pending.complete
    assert complete.participant_count == 4 and complete.pending_count == 0 and complete.complete
    assert len(pending.obligations) == len(complete.obligations) == 4
    assert pending.revision == complete.revision == 1 and pending.issuer_id == complete.issuer_id
    assert pending.target == complete.target and pending.target.kind == identity.CREDENTIAL_TARGET
    for barrier in (pending, complete):
        actual = {item.registration.participant.id: (item.generation, item.service_incarnation) for item in barrier.obligations}
        assert actual == expected
    delayed_start, = [state for state in starts if state.host == delayed]
    obligation, = [item for item in pending.obligations if item.registration.participant.id == delayed_start.world_launch.binding.participant_id]
    assert not obligation.complete and obligation.applied_revision == 0
    assert all(item.complete and item.applied_revision == 1 and not item.HasField('retirement') for item in complete.obligations)
    cancelled = [reply.state for reply in replies if reply.state.reservation_released]
    assert {state.host for state in cancelled} == {'sb16', 'sb16-2'}
    for state in cancelled:
        assert state.native_key_release and state.native_button_release
        assert state.old_actor_rejected and state.application_responded_after_cancellation
        start, = [value for value in starts if value.host == state.host]
        assert state.application == start.application
    held = max((reply.state.held_bytes for reply in replies if reply.state.feed_held and reply.state.host == delayed), default=0)
    if tag == '03':
        assert held > 0
        held_state = by_id[5].state
        assert held_state.host == delayed and held_state.feed_held and held_state.held_bytes > 0
        assert not held_state.native_key_release and not held_state.native_button_release
    return dict(exact_generations=4, pending_then_acknowledged=True, retirement_used=False,
        held_encrypted_bytes=held, both_native_applications_survived=True)

cases = []
for tag in ('01', '02', '03'):
    profiles = ('debug', 'runtime-sanitize') if tag == '03' else ('debug',)
    for profile in profiles:
        for direction in (('forward',) if tag == '01' else ('forward', 'reverse')):
            issuer = 'sb16' if direction == 'forward' else 'sb16-2'
            delayed = 'sb16-2' if direction == 'forward' else 'sb16'
            run = roots[tag] / 'runs' / (profile + '-' + direction + '01')
            item = dict(tag=tag, profile=profile, direction=direction, nodes={})
            for mode in (('controller', 'worker') if host == issuer else ('worker',)):
                path = run / (mode + '-review.json'); review = read(path)
                assert review['status'] == ('failed' if tag == '01' else 'passed')
                assert review['exit_code'] == (1 if tag == '01' else 0) and 'ended' in review
                assert review['host'] == host
                assert review['profile'] == profile and review['mode'] == mode
                assert review['command'][review['command'].index('--source') + 1] == str(roots[tag] / 'source')
                if tag != '01':
                    assert review['source_tag'] == tag
                    assert review['source_manifest_sha256'] == sha(roots[tag] / 'manifest.json')
                assert review['build_review_sha256'] == sha(roots['01'] / 'checks' / profile / 'build-review.json')
                assert review['log_sha256'] == sha(run / (mode + '.log'))
                add(path); add(run / (mode + '.log'))
                directory = run / mode; result = read(directory / 'result.json')
                assert result['status'] == ('failed' if tag == '01' else 'passed')
                item['nodes'][mode] = dict(exit_code=review['exit_code'], status=review['status'])
                if tag != '01':
                    clean_logs(directory); clean_logs(run) if mode == 'controller' else None
                    item['nodes'][mode]['witness'] = native_worker(directory) if mode == 'worker' else issuer_witness(directory, tag, delayed)
                for candidate in directory.rglob('*'):
                    if not candidate.is_file() or 'private-bundle' in candidate.parts: continue
                    assert candidate.suffix in ('.json', '.log', '.pb'), candidate
                    add(candidate)
            if tag == '01':
                add(run / 'launcher-used.py')
                if host == 'sb16':
                    assert 'Stop retained world scopes before the session broker' in (run / 'worker.log').read_text()
            cases.append(item)

related = {}
for profile in ('debug', 'runtime-sanitize'):
    root = roots['02'] / 'related' / profile; review = read(root / 'review.json')
    assert review['status'] == 'passed' and review['host'] == host and review['profile'] == profile
    assert review['build_review_sha256'] == sha(roots['01'] / 'checks' / profile / 'build-review.json')
    counts = {}
    for item in review['tests']:
        path = root / (item['name'] + '.log'); text = path.read_text()
        assert item['exit_code'] == 0 and item['log_sha256'] == sha(path)
        assert '\nOK\n' in text and 'skipped=' not in text and '... skipped' not in text
        count, = re.findall(r'^Ran (\d+) tests? in ', text, re.M); counts[item['name']] = int(count)
    assert set(counts) == {'authority-owned-participant', 'session-application'}
    clean_logs(root)
    for path in root.rglob('*'):
        if path.is_file():
            assert path.suffix == '.log' or path.name == 'review.json'; add(path)
    related[profile] = counts
assert related['debug'] == related['runtime-sanitize']

summary = dict(status='focused_passed_phase_gate_pending', host=host, candidate='03',
    source_files=347, locked_files=41, cases=cases, related=related, attachments=attachments,
    limitations=['Full M02 qualification remains pending on both hosts.',
        'The two preserved intermittent timeout causes remain unresolved.',
        'These scenarios revoke credentials across two independent worlds. They do not qualify shared-world geometry.',
        'Further cross-host crash and recovery scenarios remain phase-gate work.'])
output = base / ('crosshost-cancellation-collected-03-' + host + '.json')
with output.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = base / ('crosshost-cancellation-checks-03-' + host + '.tar.gz')
prefix = 'crosshost-cancellation-03/' + host
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(base / name, arcname=prefix + '/' + name, recursive=False)
    tar.add(output, arcname=prefix + '/collected-review.json', recursive=False)
print(json.dumps(dict(host=host, archive=str(archive), sha256=sha(archive),
    attachments=len(attachments), cases=len(cases), related=related)))
