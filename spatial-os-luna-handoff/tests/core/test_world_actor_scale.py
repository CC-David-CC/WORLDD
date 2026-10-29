"""Exercise distinct issuer-backed actors through one real TLS world."""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import sys
import time
import unittest
from unittest.mock import patch

from google.protobuf import text_format
import core_pb2 as core
import issuer_pb2 as issuer
import world_session_pb2 as session
from harness.issuer_progress import DelayedInfoIssuer
from test_world_identity import WORLD
from test_world_remote_lifecycle import RemoteLifecycleTests

SCALE_INPUT_TIMEOUT_SECONDS = 1.0 / 3.0


class WorldActorScaleTests(RemoteLifecycleTests):
    def config_file(self, endpoint_port=None, **overrides):
        relay = getattr(self, '_scale_relay', None)
        return super().config_file(endpoint_port=endpoint_port or (relay.port if relay else None), **overrides)

    def open_scale_world(self):
        evidence = Path(os.environ.get('SPATIAL_SECURITY_EVIDENCE', str(self.root))) / (self._testMethodName+'-progress.json')
        started = time.monotonic()

        def record(stage, **values):
            data = dict(stage=stage, elapsed_seconds=time.monotonic()-started, **values)
            evidence.write_text(json.dumps(data, sort_keys=True, indent=2)+'\n')
            evidence.chmod(0o600)

        actors = [(self.record, self.actor_key, self.actor_public, self.identity)]
        record('enrolling', enrolled=1)
        for index in range(1, 256):
            key, public = self.key('scale-actor-%03d' % index)
            enrollment = self.enroll(self.owner_peer,
                self.intent(public=public, kind=issuer.AGENT, label='Scale actor %03d' % index), key, public)
            approved = self.decide(self.owner_peer, enrollment).enrollment
            claims = self.claims(approved, self.info)
            actors.append((approved, key, public, claims))
            if len(actors) % 16 == 0:
                record('enrolling', enrolled=len(actors))
        self.assertEqual(len({actor[3].actor_id for actor in actors}), 256)
        if getattr(self, 'delay_scale_actor', False):
            self._scale_relay = DelayedInfoIssuer(self, delayed_operation='credential_check',
                actor_id=actors[-1][3].actor_id)
        local = self.world(admission=session.REGISTERED_ACTORS, grants=['navigate'])
        local.close()
        config = text_format.Parse(local.config_path.read_text(), session.WorldIdentityConfig())
        # Public admission does not need a duplicate list of actor IDs.
        config.ClearField('admitted_actor_ids')
        for actor in actors[1:]:
            config.actor_grants.add(actor_id=actor[3].actor_id, grants=['navigate'])
        config_text = text_format.MessageToString(config)
        self.assertLessEqual(len(config_text.encode()), 65536)
        local.config_path.write_text(config_text)
        runtime = text_format.Parse((WORLD.parent / 'runtime.textproto').read_text(), core.RuntimeConfig())
        runtime.input_timeout = SCALE_INPUT_TIMEOUT_SECONDS
        local.runtime_path = self.root / 'scale-runtime.textproto'
        local.runtime_path.write_text(text_format.MessageToString(runtime))
        self.remote_connection_limit = 256
        self.remote_idle_timeout_ms = 300000
        world = self.restart(local)
        context = self.remote_context(world)
        peers = []
        identities = []
        record('authenticating', enrolled=len(actors), authenticated=0)
        selected = actors[:-1] if getattr(self, 'delay_scale_actor', False) else actors
        for actor in selected:
            peer = self.connect_with(world, context)
            opened = self.authenticate(peer, actor[0], actor[1], actor[2])
            self.assertTrue(opened.HasField('opened'), opened)
            self.assertEqual(opened.opened.identity.actor_id, actor[3].actor_id)
            peers.append(peer)
            identities.append(opened.opened.actor_id)
            if len(peers) % 16 == 0:
                record('authenticating', enrolled=len(actors), authenticated=len(peers))
        self.assertEqual(len(set(identities)), len(selected))
        if getattr(self, 'delay_scale_actor', False):
            self._scale_slow_peer = self.connect_with(world, context)
            self._scale_slow_peer.stream.settimeout(8)
        return world, peers, identities, actors, record

    def test_256_distinct_actors_authenticate_and_remain_live(self):
        world, peers, identities, actors, record = self.open_scale_world()
        for index, peer in enumerate(peers):
            state = peer.call(inspect=session.WorldSessionInspect())
            self.assertTrue(state.HasField('state'), state)
            self.assertEqual(state.state.id, identities[index])
            if (index+1) % 16 == 0:
                record('inspecting', enrolled=len(actors), authenticated=len(peers), inspected=index+1)
        record('complete', enrolled=len(actors), authenticated=len(peers), inspected=len(peers))

    def scale_movement_window(self, delayed):
        self.retained_movement_override = True
        self.delay_scale_actor = delayed
        with patch.dict(os.environ, SPATIAL_TRACE_STOP='1'), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            world, peers, identities, actors, record = self.open_scale_world()
            before = []
            for index, peer in enumerate(peers):
                state = peer.call(inspect=session.WorldSessionInspect())
                self.assertTrue(state.HasField('state'), state)
                before.append(state.state.pose.position.x)
                peer.sequence = (index+1)*1000000
            authentication = None
            if delayed:
                actor = actors[-1]
                self._scale_relay.delay_armed.set()
                authentication = pool.submit(self.authenticate, self._scale_slow_peer, actor[0], actor[1], actor[2])
                self.assertTrue(self._scale_relay.delay_entered.wait(3), 'Delayed actor did not reach credential check')
            movement = core.ActorInputRequest(axes=[1, 0, 0, 0, 0, 0])
            sent = [[] for _ in peers]
            start = time.monotonic()
            for round_number in range(10):
                target = start + round_number*.2
                delay = target-time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                for index, peer in enumerate(peers):
                    peer.motion_refresh(movement)
                    sent[index].append(time.monotonic())
            deadline = time.monotonic()+5
            pattern = re.compile(r'role=world_motion_refresh id=(\d+).*phase=begin mono_ns=(\d+)')
            while True:
                trace = Path(world.log.name).read_text()
                matches = pattern.findall(trace)
                if len(matches) >= len(peers)*10 or time.monotonic() >= deadline:
                    break
                time.sleep(.01)
            processed = [[] for _ in peers]
            for request, stamp in matches:
                index = int(request)//1000000-1
                if 0 <= index < len(processed):
                    processed[index].append(int(stamp)/1e9)
            counts = [len(items) for items in processed]
            gaps = [later-earlier for items in processed for earlier, later in zip(items, items[1:])]
            sender_gaps = [later-earlier for items in sent for earlier, later in zip(items, items[1:])]
            pending = delayed and not self._scale_relay.delay_finished.is_set()
            record('movement_witness', enrolled=len(actors), authenticated=len(peers),
                min_refreshes=min(counts), max_refreshes=max(counts), total_refreshes=sum(counts),
                maximum_processing_gap_seconds=max(gaps, default=None),
                maximum_sender_gap_seconds=max(sender_gaps, default=None),
                scale_input_timeout_seconds=SCALE_INPUT_TIMEOUT_SECONDS,
                slow_authentication_pending=pending)
            self.assertEqual(counts, [10]*len(peers), 'Missing or duplicate processed movement refresh')
            self.assertLess(max(sender_gaps), .25, 'Test source missed its 250 ms refresh cadence')
            self.assertLess(max(gaps), SCALE_INPUT_TIMEOUT_SECONDS,
                'World processing exceeded the approved one-third-second scale input expiry')
            if delayed:
                self.assertTrue(pending, 'Slow authentication finished before movement was witnessed')
                self.assertTrue(self._scale_relay.delay_finished.wait(6), 'Slow authentication did not recover')
                result = authentication.result(timeout=3)
                self.assertTrue(result.HasField('opened'), result)
                self.assertEqual(result.opened.identity.actor_id, actors[-1][3].actor_id)
            for index, peer in enumerate(peers):
                state = peer.call(inspect=session.WorldSessionInspect())
                self.assertTrue(state.HasField('state'), state)
                self.assertGreater(state.state.pose.position.x, before[index], identities[index])
            record('movement_complete', enrolled=len(actors), authenticated=len(peers),
                moved=len(peers), total_refreshes=sum(counts), maximum_processing_gap_seconds=max(gaps),
                maximum_sender_gap_seconds=max(sender_gaps), slow_authentication_pending=pending,
                scale_input_timeout_seconds=SCALE_INPUT_TIMEOUT_SECONDS,
                slow_authentication_completed=bool(delayed and authentication.done()),
                slow_credential_delay_seconds=self._scale_relay.delay_records[0]['elapsed_seconds'] if delayed else None)

    def test_256_actors_refresh_movement_within_expiry(self):
        self.scale_movement_window(False)

    def test_255_actors_refresh_during_other_actor_authentication(self):
        self.scale_movement_window(True)


if __name__ == '__main__':
    names = [arg.rsplit('.', 1)[-1] for arg in sys.argv[1:]] or [
        'test_256_distinct_actors_authenticate_and_remain_live',
        'test_256_actors_refresh_movement_within_expiry',
        'test_255_actors_refresh_during_other_actor_authentication']
    suite = unittest.TestSuite(WorldActorScaleTests(name) for name in names)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
