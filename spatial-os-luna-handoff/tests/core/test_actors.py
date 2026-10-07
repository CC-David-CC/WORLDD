"""Actor runtime counterexamples on actual worldd processes."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from harness.rpc import Client, RpcError
from harness.wire import pb, frame, make_request, read_message, result_value

HOST, DAEMON = sys.argv[1:3]
sys.argv[1:3] = []


class ActorRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'

    def start(self, argv=None, state=None):
        client = Client(argv or [HOST], state or self.state, self.root / 'logs', timeout=4)
        self.addCleanup(client.stop)
        client.call('system.hello')
        return client

    def actor(self, client, ident='a', grants=None):
        return client.call('actor.open', {'id': ident, 'grants': ['navigate'] if grants is None else grants})

    def act(self, client, actor, seq, axes=None, op='input'):
        return client.call('actor.act', {'token': actor['token'], 'seq': seq, 'op': op,
                                        'args': {} if axes is None else {'axes': axes}})

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(RpcError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_default_entry_spawn_and_actor_management_boundary(self):
        client = self.start()
        actor = self.actor(client)
        entity = client.call('world.get', {'id': actor['id']})
        self.assertEqual(entity['kind'], 'actor')
        self.assertEqual(entity['room'], client.call('world.info')['entry_room'])
        self.assertEqual(entity['pose'], {'p': [0, 0, 0], 'q': [0, 0, 0, 1]})
        before = client.call('world.snapshot')
        self.error('IN_USE', client.call, 'actor.open', {'id': actor['id'], 'grants': []})
        self.error('INVALID_ARGUMENT', client.call, 'world.create', {'entity': {
            'id': 'arbitrary', 'kind': 'actor', 'room': entity['room'], 'pose': entity['pose']}})
        self.error('INVALID_ARGUMENT', client.call, 'world.update',
                   {'id': actor['id'], 'expected_revision': entity['revision'], 'patch': {'label': 'override'}})
        self.error('INVALID_ARGUMENT', client.call, 'world.delete',
                   {'id': actor['id'], 'expected_revision': entity['revision']})
        self.assertEqual(client.call('world.snapshot'), before)
        self.assertEqual(client.call('actor.inspect', {'token': actor['token']})['id'], actor['id'])

    def test_denied_and_invalid_actions_consume_sequence_without_refresh(self):
        client = self.start()
        denied = self.actor(client, 'denied', [])
        self.error('FORBIDDEN', self.act, client, denied, 1, [1, 0, 0, 0, 0, 0])
        self.error('STALE_SEQUENCE', self.act, client, denied, 1, op='stop')
        self.act(client, denied, 2, op='stop')
        actor = self.actor(client)
        self.act(client, actor, 1, [1, 0, 0, 0, 0, 0])
        client.call('clock.advance', {'seconds': .2})
        self.error('INVALID_ARGUMENT', self.act, client, actor, 2, [0, 0, 0, 0, 0, 2])
        self.error('STALE_SEQUENCE', self.act, client, actor, 2, op='stop')
        client.call('clock.advance', {'seconds': .1})
        result = client.call('actor.inspect', {'token': actor['token']})
        self.assertAlmostEqual(result['pose']['p'][0], .25)
        self.assertEqual(result['axes'], [0] * 6)
        self.error('UNKNOWN_METHOD', self.act, client, actor, 3, op='unknown')
        self.error('STALE_SEQUENCE', self.act, client, actor, 3, op='stop')

    def test_sequence_zero_missing_and_foreign_tokens(self):
        client = self.start()
        actor = self.actor(client)
        self.error('INVALID_ARGUMENT', self.act, client, actor, 0, op='stop')
        self.error('INVALID_ARGUMENT', client.call, 'actor.act', {'token': actor['token'], 'op': 'stop'})
        self.act(client, actor, 1, op='stop')
        other = self.start(state=self.root / 'other')
        self.error('UNAUTHORIZED', other.call, 'actor.inspect', {'token': actor['token']})
        replacement = dict(actor, token='0' * 64)
        self.error('UNAUTHORIZED', self.act, client, replacement, 1, op='stop')

    def test_checkpoint_rollback_covers_all_actors_and_revisions(self):
        client = self.start()
        a = self.actor(client, 'a')
        b = self.actor(client, 'b')
        before = client.call('world.snapshot')
        self.act(client, a, 1, [1, 0, 0, 0, 0, 0])
        self.act(client, b, 1, [0, 1, 0, 0, 0, 0])
        client.call('clock.advance', {'seconds': .1})
        with sqlite3.connect(self.state / 'world.db') as db:
            db.execute("CREATE TRIGGER checkpoint_failure BEFORE UPDATE ON entities WHEN old.id='b' BEGIN SELECT RAISE(ABORT,'fail after actor a'); END")
        self.error('STORAGE_ERROR', client.call, 'world.checkpoint')
        self.assertEqual(client.call('world.snapshot'), before)
        self.assertAlmostEqual(client.call('actor.inspect', {'token': a['token']})['pose']['p'][0], .1)
        with sqlite3.connect(self.state / 'world.db') as db:
            db.execute('DROP TRIGGER checkpoint_failure')
        result = client.call('world.checkpoint')
        self.assertEqual(result['world_revision'], before['world_revision'] + 1)
        after = client.call('world.snapshot')
        for entity in after['entities']:
            if entity['kind'] == 'actor':
                self.assertEqual(entity['revision'], 2)
                self.assertEqual(entity['world_revision'], result['world_revision'])
        client.call('world.checkpoint')
        self.assertEqual(client.call('world.snapshot'), after)
        data = (self.state / 'world.db').read_bytes()
        self.assertNotIn(a['token'].encode(), data)
        self.assertNotIn(b['token'].encode(), data)

    def test_uncheckpointed_motion_and_old_token_do_not_survive(self):
        client = self.start()
        a = self.actor(client)
        self.act(client, a, 1, [1, 0, 0, 0, 0, 0])
        client.call('clock.advance', {'seconds': .1})
        client.stop(crash=True)
        client = self.start()
        self.error('UNAUTHORIZED', client.call, 'actor.inspect', {'token': a['token']})
        b = self.actor(client)
        self.assertNotEqual(a['token'], b['token'])
        self.assertEqual(client.call('world.get', {'id': b['id']})['pose']['p'], [0, 0, 0])
        client.call('clock.advance', {'seconds': 1})
        self.assertEqual(client.call('actor.inspect', {'token': b['token']})['axes'], [0] * 6)

    def test_close_and_reopen_preserves_live_pose_with_new_grants(self):
        client = self.start()
        a = self.actor(client)
        self.act(client, a, 1, [1, 0, 0, 0, 0, 0])
        client.call('clock.advance', {'seconds': .1})
        client.call('actor.close', {'token': a['token']})
        b = client.call('actor.open', {'id': a['id'], 'room': 'ignored-respawn', 'grants': []})
        self.assertNotEqual(a['token'], b['token'])
        self.assertAlmostEqual(client.call('actor.inspect', {'token': b['token']})['pose']['p'][0], .1)
        self.error('FORBIDDEN', self.act, client, b, 1, [1, 0, 0, 0, 0, 0])
        self.error('UNAUTHORIZED', self.act, client, a, 2, op='stop')

    def test_runtime_configuration_changes_motion(self):
        path = self.root / 'profile.textproto'
        path.write_text('linear_gain: 3\nangular_gain: 2\ndeadzone: 0\ninput_timeout: 0.1\n')
        client = self.start([DAEMON, '--fixture-clock', '--config', str(path)])
        actor = self.actor(client)
        self.act(client, actor, 1, [.5, 0, 0, 0, 0, 0])
        client.call('clock.advance', {'seconds': 1})
        self.assertAlmostEqual(client.call('actor.inspect', {'token': actor['token']})['pose']['p'][0], .15)

    def test_bad_configuration_fails_before_creating_world(self):
        path = self.root / 'bad.textproto'
        for invalid in ['linear_gain: 1\nunknown: 1', 'linear_gain: 1\nangular_gain: 1\ndeadzone: 1\ninput_timeout: .25']:
            path.write_text(invalid)
            result = subprocess.run([DAEMON, '--state-dir', str(self.state), '--config', str(path)],
                                    input=b'', capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.state / 'world.db').exists())

    def test_real_clock_expires_motion_and_disallows_clock_override(self):
        client = self.start([DAEMON])
        actor = self.actor(client)
        self.error('FORBIDDEN', client.call, 'clock.advance', {'seconds': 1})
        self.act(client, actor, 1, [1, 0, 0, 0, 0, 0])
        deadline = time.monotonic() + 2
        while True:
            result = client.call('actor.inspect', {'token': actor['token']})
            if result['axes'] == [0] * 6:
                break
            self.assertLess(time.monotonic(), deadline, 'Motion did not expire on the real clock')
            time.sleep(.01)
        self.assertAlmostEqual(result['pose']['p'][0], .25, places=5)
        previous = result['pose']
        time.sleep(.05)
        self.assertEqual(client.call('actor.inspect', {'token': actor['token']})['pose'], previous)

    def test_partial_request_does_not_extend_motion_lifetime(self):
        proc = subprocess.Popen([DAEMON, '--state-dir', str(self.state)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=4)
        self.addCleanup(cleanup)
        def call(ident, method, params):
            request = make_request(method, params)
            request.request_id = ident
            proc.stdin.write(frame(request)); proc.stdin.flush()
            reply = read_message(proc.stdout, pb.Reply)
            self.assertEqual(reply.request_id, ident)
            self.assertNotEqual(reply.WhichOneof('outcome'), 'error')
            return result_value(reply)
        actor = call(1, 'actor.open', {'id': 'a', 'grants': ['navigate']})
        call(2, 'actor.act', {'token': actor['token'], 'seq': 1, 'op': 'input', 'args': {'axes': [1, 0, 0, 0, 0, 0]}})
        hello = make_request('system.hello', {})
        hello.request_id = 3
        encoded = frame(hello)
        proc.stdin.write(encoded[:1]); proc.stdin.flush()
        time.sleep(.35)
        proc.stdin.write(encoded[1:]); proc.stdin.flush()
        self.assertEqual(read_message(proc.stdout, pb.Reply).request_id, 3)
        result = call(4, 'actor.inspect', {'token': actor['token']})
        self.assertEqual(result['axes'], [0] * 6)
        self.assertAlmostEqual(result['pose']['p'][0], .25, places=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
