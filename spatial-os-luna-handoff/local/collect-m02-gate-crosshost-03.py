"""Collect candidate02 cross-host results; preserve failures and distinguish unrun cases."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import sys
import tarfile
from m02_crosshost_qualification03 import load_qualification

parser=argparse.ArgumentParser()
parser.add_argument("--qualification",required=True,choices=("m02-phase-gate-02","m02-untraced-03"))
args=parser.parse_args()

host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m02-gate-crosshost-03';gate=base/'m02-phase-gate-02'
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
read=lambda path:json.loads(path.read_bytes())
manifest,summary,local_review,qualification=load_qualification(base,host,args.qualification)
assert manifest['candidate']==summary['candidate']=='m02-phase-gate-02'
assert all(sha(gate/'source'/name)==digest for name,digest in manifest['files'].items())
assert all(manifest['files'][name]==digest for name,digest in manifest['locked_files'].items())
assert sha(Path(summary['server']['path']))==summary['server']['sha256']
for profile in ('debug','runtime-sanitize'):
    assert all(sha(gate/profile/'build'/name)==digest for name,digest in summary['profiles'][profile]['binaries'].items())
binding=read(root/'bindings-review.json')
assert binding['exit_code']==0 and binding['schema_sha256']==manifest['files']['tests/proto/crosshost_revocation.proto']
assert sha(root/'python/crosshost_revocation_pb2.py')==binding['binding_sha256']
sys.path[:0]=[str(root/'python'),str(gate/'debug/build/python')]
import crosshost_revocation_pb2 as wire
import issuer_pb2 as identity
import launch_pb2 as launch
attachments={}

def add(path):
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base.resolve())
    assert 'private-bundle' not in path.parts and path.suffix in ('.log','.json','.pb','.py','.proto')
    data=path.read_bytes();assert (b'PRIVATE '+b'KEY-----') not in data and (b'Bearer '+b'fleet-') not in data
    attachments[path.relative_to(base).as_posix()]=sha(path)

for attempt in {gate,base/args.qualification}:
    for name in ('manifest.json','summary.json','primary-review.json'):add(attempt/name)
add(base/'m02_crosshost_qualification03.py')
for name in ('bindings-review.json','bindings.log'):add(root/name)
for name in ('run-m02-gate-crosshost-03.py','collect-m02-gate-crosshost-03.py'):add(base/name)
for name in ('tests/native/crosshost_revocation.py','tests/proto/crosshost_revocation.proto'):add(gate/'source'/name)
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

cases=[]
for profile in ('debug','runtime-sanitize'):
    for scenario in ('delay','crash'):
        for direction in ('forward','reverse'):
            issuer='sb16' if direction=='forward' else 'sb16-2'
            delayed='sb16-2' if direction=='forward' else 'sb16'
            run=root/'runs'/(profile+'-'+scenario+'-'+direction+'01')
            case=dict(profile=profile,scenario=scenario,direction=direction,nodes={})
            for mode in (('controller','worker') if host==issuer else ('worker',)):
                node=dict(status='unrun');case['nodes'][mode]=node
                record_path=run/(mode+'-review.json')
                if not record_path.exists():continue
                record=read(record_path)
                assert 'ended' in record and record['status']!='running','Node is not terminal; retain its original process handle'
                assert (record['host'],record['profile'],record['scenario'],record['direction'],record['mode'])==(host,profile,scenario,direction,mode)
                assert record['source_manifest_sha256']==sha(gate/'manifest.json')
                assert record['qualification']==qualification
                assert record['qualification_helper_sha256']==sha(base/'m02_crosshost_qualification03.py')
                assert record['bindings_review_sha256']==sha(root/'bindings-review.json')
                assert record['log_sha256']==sha(run/(mode+'.log'))
                assert record['runner_sha256']==sha(base/'run-m02-gate-crosshost-03.py')
                add(record_path);add(run/(mode+'.log'))
                node.update(status='failed',exit_code=record.get('exit_code'),launcher_status=record['status'])
                directory=run/mode
                for path in directory.rglob('*'):
                    if path.is_file() and 'private-bundle' not in path.parts:add(path)
                if record.get('exit_code')==0 and record['status']=='passed':
                    try:
                        result=read(directory/'result.json');assert result['status']=='passed'
                        node['witness']=native(directory,scenario,delayed) if mode=='worker' else controller(directory,scenario,delayed)
                        for path in list(directory.rglob('*.log'))+[run/(mode+'.log')]:
                            if 'private-bundle' in path.parts:continue
                            data=path.read_bytes()
                            assert not any(marker in data for marker in (b'ERROR: AddressSanitizer',b'ERROR: LeakSanitizer',b'AddressSanitizer:DEADLYSIGNAL',b'runtime error:',b'Traceback (most recent call last)',b'FAILED (')),path
                        node['status']='passed'
                    except Exception as error:
                        node['witness_error']=type(error).__name__+': '+str(error)
            case['status']='passed' if all(n['status']=='passed' for n in case['nodes'].values()) else 'failed' if any(n['status']=='failed' for n in case['nodes'].values()) else 'unrun'
            cases.append(case)
review=dict(host=host,status='reviewed_crosshost_evidence_phase_pending',candidate=manifest['candidate'],commit=manifest['commit'],
    qualification=qualification,cases=cases,counts={s:sum(c['status']==s for c in cases) for s in ('passed','failed','unrun')},attachments=attachments,
    phase_complete=False,historical_failures_resolved=False)
path=root/('collected-review-'+host+'.json')
with path.open('xb') as stream:stream.write((json.dumps(review,indent=2)+'\n').encode())
archive=base/('m02-gate-crosshost-evidence-03-'+host+'.tar.gz')
with tarfile.open(archive,'x:gz') as tar:
    for name in sorted(attachments):tar.add(base/name,arcname=host+'/'+name,recursive=False)
    tar.add(path,arcname=host+'/collected-review.json',recursive=False)
print(json.dumps(dict(host=host,archive=str(archive),sha256=sha(archive),counts=review['counts'],attachments=len(attachments))))
