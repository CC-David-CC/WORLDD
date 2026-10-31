"""Three ordinary authenticated actors through production shared action owners."""
import os
import json
from pathlib import Path
import select
import struct
import subprocess
import sys
import threading
import time
import unittest
from google.protobuf import text_format

SCRIPTED = Path(sys.argv.pop(7)).resolve()
import test_host_continuous_capture as continuous
import actor_session_client_pb2 as tls
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as issuer
import scripted_actor_pb2 as scripted
import world_session_pb2 as world
from harness.issuer_progress import DelayedInfoIssuer
from harness.wire import frame


class ScriptedPeer:
    def __init__(self, fixture, number, configuration):
        self.fixture = fixture
        self.sequence = 0
        self.expected_exit = 0
        self.lock = threading.RLock()
        self.timings = []
        self.motion_refresh_send_times = []
        self.motions = []
        self.log_path = fixture.root / ('scripted-' + str(number) + '.log')
        with self.log_path.open('wb') as log:
            self.process = subprocess.Popen([str(SCRIPTED), '--config', str(configuration)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, close_fds=True)
        fixture.addCleanup(self.close)
        self.initial = self.call(inspect=True)
        fixture.assertTrue(self.initial.HasField('state'), (self.initial, self.log_path.read_text()))
        fixture.assertTrue(self.initial.context.available)

    def receive(self):
        deadline = time.monotonic() + 15
        def exact(size):
            data = bytearray()
            while len(data) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise AssertionError('Scripted actor reply exceeded its deadline')
                part = os.read(self.process.stdout.fileno(), size - len(data))
                if not part:
                    raise AssertionError('Scripted actor closed its reply: ' + self.log_path.read_text())
                data.extend(part)
            return bytes(data)
        size, = struct.unpack('!I', exact(4))
        self.fixture.assertGreater(size, 0)
        self.fixture.assertLessEqual(size, 1048576)
        reply = scripted.ScriptedActorReply.FromString(exact(size))
        self.fixture.assertEqual(reply.protocol_version, 1)
        return reply

    def call(self, generation=0, request_id=None, *, delay_client_completion_seconds=0.0, **operation):
        with self.lock:
            started = time.monotonic()
            try:
                return self._call(generation, request_id,
                    delay_client_completion_seconds=delay_client_completion_seconds, **operation)
            finally:
                self.timings.append((started, time.monotonic() - started, next(iter(operation), 'none')))

    def _call(self, generation=0, request_id=None, *, delay_client_completion_seconds=0.0, **operation):
        if not 0.0 <= delay_client_completion_seconds <= 5.0:
            raise ValueError('Client completion delay must be within 0..5 seconds')
        if request_id is None:
            self.sequence += 1
            request_id = self.sequence
        request = scripted.ScriptedActorRequest(protocol_version=1, request_id=request_id,
            generation=generation, **operation)
        self.process.stdin.write(frame(request))
        self.process.stdin.flush()
        if 'propose' in operation and operation['propose'].HasField('input'):
            self.motion_refresh_send_times.append(time.monotonic())
        if delay_client_completion_seconds:
            # Hold the actor call open after submitting its request. This models
            # a slow caller/reply path without blocking worldd or the renderer.
            time.sleep(delay_client_completion_seconds)
        reply = self.receive()
        self.fixture.assertEqual(reply.request_id, request_id)
        return reply

    def act(self, generation=0, *, delay_client_completion_seconds=0.0, **proposal):
        return self.call(generation, delay_client_completion_seconds=delay_client_completion_seconds,
            propose=core.ActorActionRequest(**proposal))

    def close(self):
        for motion in self.motions:
            motion.close()
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            result = self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
            raise AssertionError('Scripted actor did not close its owned transport')
        self.process.stdout.close()
        data = self.log_path.read_text()
        target = os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            directory = Path(target)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / (self.fixture._testMethodName + '-' + self.log_path.name)).write_text(data)
            timing = '\n'.join(f'{start:.9f} {duration:.9f} {operation}'
                               for start, duration, operation in self.timings)
            (directory / (self.fixture._testMethodName + '-' + self.log_path.stem + '-rpc-timing.log')).write_text(timing + '\n')
            sends = '\n'.join(f'{value:.9f}' for value in self.motion_refresh_send_times)
            (directory / (self.fixture._testMethodName + '-' + self.log_path.stem + '-input-send-times.log')).write_text(sends + '\n')
        self.fixture.assertEqual(result, self.expected_exit, data)
        for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
            self.fixture.assertNotIn(marker, data)

    def motion(self, axes):
        refresh = MotionRefresh(self, axes)
        self.motions.append(refresh)
        return refresh


class MotionRefresh:
    """A real scripted action loop. Motion still expires when refresh stops."""
    def __init__(self, peer, axes):
        self.peer = peer
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.failure = None
        def run():
            try:
                while not self.stopping.is_set():
                    peer.fixture.accepted(peer.act(input=core.ActorInputRequest(axes=axes)))
                    self.ready.set()
                    self.stopping.wait(.05)
            except BaseException as error:
                self.failure = error
                self.ready.set()
        self.thread = threading.Thread(target=run, name='scripted-motion-refresh')
        self.thread.start()
        if not self.ready.wait(15):
            self.close()
            raise AssertionError('Scripted motion refresh did not start')
        self.check()

    def check(self):
        if self.failure is not None:
            raise AssertionError('Scripted motion refresh failed') from self.failure

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=16)
        if self.thread.is_alive():
            raise AssertionError('Scripted motion refresh did not stop')
        self.check()


class ScriptedActorTests(continuous.ContinuousCaptureTests):
    def setUp(self):
        super().setUp()
        self.agents = []
        for number in range(2):
            key, public = self.issuer.key('scripted-agent-' + str(number))
            intent = self.issuer.intent(public=public, kind=issuer.AGENT, label='M10 scripted agent ' + str(number))
            enrollment = self.issuer.enroll(self.issuer.peer, intent, key=key, public=public)
            record = self.issuer.decide(self.issuer.peer, enrollment).enrollment
            claims = self.issuer.claims(record, self.issuer.info)
            principal = grants.ActorRef(issuer_id=claims.issuer_id, actor_id=claims.actor_id)
            self.agents.append((key, record, principal))
        self.assertEqual(len({bytes(agent[2].actor_id) for agent in self.agents} | {bytes(self.principal.actor_id)}), 3)
        self.agent_rights = [[grants.WORLD_ENTER, grants.WORLD_NAVIGATE, grants.WORLD_OBSERVE,
                              grants.WORLD_MANIPULATE] for _ in self.agents]
        self.script_number = 0

    def make_world(self, suffix, **options):
        # The inherited two-world fixture reuses its secondary participant ID.
        # A third independently authoritative world needs a third service identity.
        previous = getattr(self, 'world_participant_identity', None)
        if suffix == '-denied':
            self.world_participant_identity = b'd' * 32
        try:
            return super().make_world(suffix, **options)
        finally:
            if previous is None:
                if hasattr(self, 'world_participant_identity'):
                    del self.world_participant_identity
            else:
                self.world_participant_identity = previous

    def world_query_config(self):
        relay=getattr(self,'_info_relay',None)
        return self.issuer.config_file(endpoint_port=relay.port if relay else None)

    def identity_config(self, config, suffix, admitted):
        # Explicit fixture provisioning. No actor receives this management boundary.
        directory = self.root / ('host-world-authority' + suffix)
        directory.mkdir(mode=0o700)
        seed = world.WorldAuthoritySeed(protocol_version=1, authority_directory=str(directory))
        principals = [self.principal] + [value[2] for value in self.agents]
        rights = [[grants.WORLD_ENTER, grants.WORLD_NAVIGATE, grants.WORLD_OBSERVE,
                   grants.WORLD_MANIPULATE]] + self.agent_rights
        for principal, allowed in zip(principals, rights):
            seed.roots.add(subject=principal,
                scope=grants.Scope(domain=grants.Domain(kind=grants.WORLD, id=self.world_domain)),
                rights=allowed if admitted else [grants.WORLD_OBSERVE])
        self.assertEqual(len(seed.roots), 3)
        binary = config.launch.executable
        result = subprocess.run([binary, '--state-dir', str(self.root / ('world' + suffix)),
            '--init-authority', str(self.config_file('scripted-world-seed' + suffix, seed))],
            capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = world.WorldIdentityConfig(protocol_version=1,
            issuer_client_config=str(self.world_query_config()),
            participant_runtime_config=config.child_participant_config,
            admission=world.EXPLICIT_ACTORS, authority_directory=str(directory))
        if os.environ.get('SPATIAL_TEST_RETAINED_MOVEMENT') == '1' or getattr(self,'retained_movement_override',False):
            policy.enable_retained_movement = True
        path = self.config_file('scripted-world-policy' + suffix, policy)
        config.launch.arguments.extend(['--identity-config', str(path)])
        runtime = text_format.Parse(Path(binary).with_name('runtime.textproto').read_text(), core.RuntimeConfig())
        self.assertEqual(runtime.input_timeout, .25)
        config.launch.arguments.extend(['--config', str(self.config_file('scripted-runtime' + suffix, runtime))])

    def scripted_peer(self, agent_index):
        key, record, _ = self.agents[agent_index]
        config = scripted.ScriptedActorConfig(protocol_version=1, initial_world_id=self.worlds[0][0].world_id)
        self.script_number += 1
        for index, (retained, _, _) in enumerate(self.worlds):
            client = text_format.Parse(Path(retained.actor_tls_config).read_text(), tls.ActorSessionClientTlsConfig())
            client.actor_signing_key_path = str(key)
            client.actor_credential.CopyFrom(record.credential)
            path = self.config_file(f'scripted-{self.script_number}-world-{index}', client)
            config.worlds.add(world_id=retained.world_id, tls_config_path=str(path))
        path = self.config_file('scripted-' + str(self.script_number), config)
        peer = ScriptedPeer(self, self.script_number, path)
        self.assertTrue(self.status().capture_active, self.host_log.read_text())
        return peer

    def accepted(self, reply):
        self.assertTrue(reply.HasField('action'), reply)
        self.assertTrue(reply.action.accepted, reply)
        self.assertTrue(reply.context.available, reply)
        return reply.action

    def wait_autonomous_human_motion(self, axes):
        # Checked key handling precedes the next scheduled navigation tick.
        # Inspection observes that tick. It never refreshes movement itself.
        deadline = time.monotonic() + 1
        while True:
            status = self.status()
            self.assertTrue(status.capture_active)
            self.assertFalse(status.capture_retired)
            state = self.state()
            if list(state.axes) == axes:
                return state
            self.assertLess(time.monotonic(), deadline, state)
            time.sleep(.01)

    def test_human_and_two_scripted_actors_navigate_independently(self):
        self.retained_movement_override = True
        human = self.ready_host()
        policy = text_format.Parse((self.root/'scripted-world-policy.textproto').read_text(),
                                   world.WorldIdentityConfig())
        self.assertTrue(policy.enable_retained_movement,
                        'Three-actor liveness check requires the M10 refresh path')
        producer = self.producer()
        first, second = self.scripted_peer(0), self.scripted_peer(1)
        self.assertEqual(len({human.inspection.state.id, first.initial.state.id, second.initial.state.id}), 3)
        self.wait_processed(producer.key('w'))
        self.wait_autonomous_human_motion([0, 0, -1, 0, 0, 0])
        first_motion = first.motion([1, 0, 0, 0, 0, 0])
        second_motion = second.motion([0, 1, 0, 0, 0, 0])
        self.assertEqual(list(self.state().axes), [0, 0, -1, 0, 0, 0])
        self.assertEqual(list(first.call(inspect=True).state.axes), [1, 0, 0, 0, 0, 0])
        self.assertEqual(list(second.call(inspect=True).state.axes), [0, 1, 0, 0, 0, 0])
        first_motion.close()
        self.accepted(first.act(stop=core.ActorStopRequest()))
        self.assertEqual(list(first.call(inspect=True).state.axes), [0] * 6)
        self.assertEqual(list(second.call(inspect=True).state.axes), [0, 1, 0, 0, 0, 0])
        self.assertEqual(list(self.state().axes), [0, 0, -1, 0, 0, 0])
        second_motion.close()
        self.accepted(second.act(stop=core.ActorStopRequest()))
        observed = self.accepted(first.act(observe=core.ActorObserveRequest(include_pixels=True, width=64, height=64)))
        self.assertEqual(len(observed.observation.image.rgb8), 64 * 64 * 3)
        self.assertEqual(observed.observation.actor, first.initial.context.actor_id)
        first.close()
        self.assertTrue(second.call(inspect=True).context.available)
        self.assertTrue(self.status().capture_active)
        second.close()
        self.stop()
        self.shutdown_checked()

    def test_human_refresh_and_two_scripted_actors_survive_pending_presentation_authority(self):
        self.retained_movement_override=True
        selected=os.environ.get('SPATIAL_TEST_PRESENTATION_CONTINUATIONS','1')
        self.assertIn(selected,('0','1'));self.presentation_continuations_override=selected=='1'
        self._info_relay=DelayedInfoIssuer(self.issuer, delayed_operation="credential_check", actor_id=self.principal.actor_id)
        human=self.ready_host()
        human_world=self.worlds[0][0]
        human_actor_id=human.inspection.state.id
        policy=text_format.Parse((self.root/'scripted-world-policy.textproto').read_text(),world.WorldIdentityConfig())
        self.assertTrue(policy.enable_retained_movement,'Presentation-specific delay requires retained movement')
        self.assertEqual(self.presentation_continuations_selected,self.presentation_continuations_override)
        producer=self.producer()
        first,second=self.scripted_peer(0),self.scripted_peer(1)
        self.wait_processed(producer.key('w'))
        self.wait_autonomous_human_motion([0,0,-1,0,0,0])
        first_motion=first.motion([1,0,0,0,0,0]);second_motion=second.motion([0,1,0,0,0,0])
        self._info_relay.delay_armed.set()
        self.assertTrue(self._info_relay.delay_entered.wait(3),'Human presentation credential check was not held')
        self.assertFalse(self._info_relay.delay_finished.is_set())
        pose_before=human_world.management('world.get',id=human_actor_id)['pose']['p']
        pose_samples=[];pose_stop=threading.Event()
        def collect_human_pose_during_authentication():
            while not pose_stop.is_set() and not self._info_relay.delay_finished.is_set():
                try:
                    position=human_world.management('world.get',id=human_actor_id)['pose']['p']
                    pose_samples.append((time.monotonic(),position))
                except Exception as error:
                    pose_samples.append((time.monotonic(),{'error':str(error)}))
                pose_stop.wait(.05)
        pose_thread=threading.Thread(target=collect_human_pose_during_authentication,
            name='independent-human-motion-witness')
        pose_thread.start()
        self.addCleanup(pose_thread.join,2)
        self.addCleanup(pose_stop.set)
        input_started=time.monotonic()
        sequence=producer.key('d');completed=threading.Event();result={}
        def wait_for_capture():
            started=input_started
            try:
                status=self.wait_accepted(sequence,timeout=.35)
                result['accepted_sequence']=status.accepted_capture_sequence
                result['processed_sequence_at_acceptance']=status.processed_capture_sequence
                result['capture_active']=status.capture_active
            except BaseException as error:result['error']=str(error)
            finally:
                result['elapsed_seconds']=time.monotonic()-started;completed.set()
        thread=threading.Thread(target=wait_for_capture,name='checked-human-refresh')
        thread.start();fast=completed.wait(max(0,.35-(time.monotonic()-input_started)))
        pending_at_receipt=not self._info_relay.delay_finished.is_set()
        try:
            self.assertTrue(self._info_relay.delay_finished.wait(5))
        finally:
            pose_stop.set();pose_thread.join(2)
            thread.join(6)
            if thread.is_alive():raise AssertionError('Checked human capture query did not settle')
            evidence=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
            if evidence:
                target=Path(evidence);target.mkdir(parents=True,exist_ok=True)
                (target/(self._testMethodName+'-scheduling.json')).write_text(json.dumps(dict(
                    accepted_within_dispatch_budget=fast,dispatch_budget_seconds=.35,
                    authority_pending_at_input_acceptance=pending_at_receipt,
                    human=result,input_request_monotonic=input_started,delay_records=self._info_relay.delay_records,
                    human_pose_before=pose_before,human_pose_samples=pose_samples,
                    retained_movement=policy.enable_retained_movement,
                    presentation_continuations=self.presentation_continuations_selected),indent=2)+'\n')
        self.assertTrue(fast,'Human input intent receipt waited for pending presentation authority beyond350ms')
        self.assertTrue(pending_at_receipt,'Human capture receipt waited for unrelated presentation authority completion')
        self.assertNotIn('error',result);self.assertTrue(result['capture_active'])
        self.assertGreaterEqual(result['accepted_sequence'],sequence)
        processed=self.wait_processed(sequence)
        self.assertGreaterEqual(processed.processed_capture_sequence,sequence,
            'Accepted human input never completed checked actor handling after authority recovered')
        self.assertEqual(list(self.state().axes),[1,0,-1,0,0,0])
        relay_end=self._info_relay.delay_records[0]['end_monotonic']
        during=[position for observed,position in pose_samples if input_started<=observed<relay_end and 'error' not in position]
        self.assertTrue(any(position[0]>pose_before[0]+.05 and position[2]<pose_before[2]-.05 for position in during),
            'Independent world state did not show human movement during the held authorization exchange')
        first_motion.close();second_motion.close()
        delay_start=self._info_relay.delay_records[0]['start_monotonic']
        delay_end=self._info_relay.delay_records[0]['end_monotonic']
        for peer in (first,second):
            sends=peer.motion_refresh_send_times
            during=[value for value in sends if delay_start<=value<=delay_end]
            self.assertGreaterEqual(len(during),12,'Scripted actor did not keep refreshing during held presentation authority')
            before=[value for value in sends if value<delay_start]
            after=[value for value in sends if value>delay_end]
            self.assertTrue(before and after,'Scripted actor refresh did not cover both edges of held authority')
            window=[before[-1],*during,after[0]]
            gaps=[later-earlier for earlier,later in zip(window,window[1:])]
            self.assertLess(max(gaps),.35,
                'Scripted actor input sends paused longer than the approved one-third-second observation window')
            self.accepted(peer.act(stop=core.ActorStopRequest()));peer.close()
        self.stop();self.shutdown_checked()

    def test_denied_human_transitions_restore_source_capture_and_pending_frames(self):
        from host_actor_controller_pb2 import HostActorTransitionRequest
        initial=self.ready_host(denied=True);producer=self.producer()
        retained=self.retained_pids();denied=self.worlds[1][0].world_id
        for generation in range(3):
            old=self.status(generation);self.stop(generation)
            reply=self.shared('transition_proposal',generation,HostActorTransitionRequest(
                protocol_version=1,transition_id=os.urandom(32),destination_world_id=denied))
            self.assertTrue(reply.accepted,reply)
            self.assertFalse(reply.transition.actor_transition.destination_activation_confirmed,reply)
            self.assertTrue(reply.transition.actor_control_available,reply)
            self.assertEqual(reply.generation,generation+1)
            producer.close();producer=self.producer(generation+1)
            status=self.status(generation+1)
            self.assertGreater(status.capture_generation,old.capture_generation)
            self.assertGreater(status.capture_source_slot,old.capture_source_slot)
            self.wait_processed(producer.key('w'),generation+1)
            self.assertTrue(self.shared('controls_tick',generation+1).accepted)
            state=self.state(generation+1)
            self.assertEqual(state.id,initial.inspection.state.id)
            self.assertEqual(list(state.axes),[0,0,-1,0,0,0])
            self.assertEqual(self.retained_pids(),retained)
        self.stop(3);self.shutdown_checked()

    def test_proposals_cannot_choose_identity_sequence_or_bypass_authority(self):
        self.agent_rights[1] = [grants.WORLD_ENTER, grants.WORLD_OBSERVE]
        self.ready_host()
        self.producer()
        first, second = self.scripted_peer(0), self.scripted_peer(1)
        before = first.call(inspect=True).state
        for proposal in [core.ActorActionRequest(token='another-actor', stop=core.ActorStopRequest()),
                         core.ActorActionRequest(seq=900, stop=core.ActorStopRequest())]:
            refused = first.call(propose=proposal)
            self.assertEqual(refused.error.code, core.INVALID_ARGUMENT, refused)
            self.assertTrue(refused.context.available)
        replay = first.call(request_id=1, inspect=True)
        self.assertEqual(replay.error.code, core.INVALID_ARGUMENT, replay)
        stale = first.call(generation=99, inspect=True)
        self.assertEqual(stale.error.code, core.INVALID_ARGUMENT, stale)
        self.assertEqual(first.call(inspect=True).state.pose, before.pose)
        denied = second.act(input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0]))
        self.assertEqual(denied.error.code, core.FORBIDDEN, denied)
        self.assertTrue(denied.cancellation_confirmed)
        self.assertTrue(denied.context.available)
        self.accepted(second.act(stop=core.ActorStopRequest()))
        self.assertEqual(list(second.call(inspect=True).state.axes), [0] * 6)
        first.close()
        second.close()
        self.shutdown_checked()

    def test_a_b_a_and_denied_destination_keep_independent_contexts(self):
        self.ready_host(second=True, denied=True)
        self.producer()
        first, second = self.scripted_peer(0), self.scripted_peer(1)
        starts = self.retained_pids()
        a, b, denied = [value.world_id for value, _, _ in self.worlds]
        for generation, destination in [(0, b), (1, a), (2, denied)]:
            reply = first.call(generation, transition=scripted.ScriptedActorTransition(
                transition_id=os.urandom(32), destination_world_id=destination))
            self.assertTrue(reply.HasField('transition'), reply)
            self.assertTrue(reply.context.available, reply)
            self.assertEqual(reply.context.generation, generation + 1)
            self.assertEqual(reply.context.actor_id, first.initial.context.actor_id)
            self.assertEqual(reply.transition.destination_activation_confirmed, destination != denied)
            self.assertEqual(reply.context.world_id, a if destination == denied else destination)
            self.assertEqual(first.call(generation + 1, inspect=True).state.workspace, '')
            stale = first.act(generation, stop=core.ActorStopRequest())
            self.assertEqual(stale.error.code, core.INVALID_ARGUMENT, stale)
            self.accepted(first.act(generation + 1, stop=core.ActorStopRequest()))
            self.assertEqual(second.call(inspect=True).context.world_id, a)
            self.assertTrue(self.status().capture_active)
            self.assertEqual(self.retained_pids(), starts)
        first.close()
        second.close()
        self.shutdown_checked()

    def test_malformed_input_and_controller_eof_preserve_other_actors(self):
        self.ready_host()
        self.producer()
        first, second = self.scripted_peer(0), self.scripted_peer(1)
        first.process.stdin.write(struct.pack('!I', 1) + b'\x80')
        first.process.stdin.flush()
        malformed = first.receive()
        self.assertEqual(malformed.request_id, 0)
        self.assertEqual(malformed.error.code, core.INVALID_ARGUMENT, malformed)
        self.assertTrue(first.call(inspect=True).context.available)
        self.accepted(first.act(input=core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0])))
        first.close()
        replacement = self.scripted_peer(0)
        self.assertEqual(replacement.initial.context.actor_id, first.initial.context.actor_id)
        self.assertEqual(list(replacement.initial.state.axes), [0] * 6)
        replacement.process.stdin.write(struct.pack('!I', 1048577))
        replacement.process.stdin.flush()
        replacement.expected_exit = 1
        self.assertEqual(replacement.process.wait(timeout=15), 1)
        self.assertTrue(second.call(inspect=True).context.available)
        self.assertTrue(self.status().capture_active)
        second.close()
        replacement.close()
        self.shutdown_checked()

    def test_held_human_motion_survives_other_actor_authentication(self):
        self.ready_host()
        producer = self.producer()
        self.wait_processed(producer.key('w'))
        self.wait_autonomous_human_motion([0, 0, -1, 0, 0, 0])
        retained = self.retained_pids()
        first, second = self.scripted_peer(0), self.scripted_peer(1)
        for _ in range(3):
            self.assertEqual(list(self.state().axes), [0, 0, -1, 0, 0, 0])
            self.assertEqual(self.retained_pids(), retained)
            self.assertTrue(self.status().capture_active)
            first.close()
            first = self.scripted_peer(0)
            self.assertTrue(second.call(inspect=True).context.available)
        first.close()
        second.close()
        self.stop()
        self.shutdown_checked()

    def test_configuration_rejects_fifo_symlink_and_public_parent_before_authentication(self):
        fifo = self.root / 'scripted-config-fifo'
        os.mkfifo(fifo, 0o600)
        ordinary = self.root / 'scripted-config-regular'
        ordinary.write_text('protocol_version: 1\n')
        ordinary.chmod(0o600)
        symlink = self.root / 'scripted-config-link'
        symlink.symlink_to(ordinary)
        public = self.root / 'scripted-public-parent'
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        exposed = public / 'configuration'
        exposed.write_bytes(ordinary.read_bytes())
        exposed.chmod(0o600)
        for path, detail in [(fifo, 'Unsafe or missing TLS material'),
                             (symlink, 'Unsafe or missing TLS material'),
                             (exposed, 'owned private parent directory')]:
            with self.subTest(kind=path.name):
                result = subprocess.run([str(SCRIPTED), '--config', str(path)],
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')
                self.assertIn(detail, result.stderr.decode())


if __name__ == '__main__':
    names = [name for name in ScriptedActorTests.__dict__ if name.startswith('test_')]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ScriptedActorTests(name) for name in names))
    raise SystemExit(not result.wasSuccessful())
