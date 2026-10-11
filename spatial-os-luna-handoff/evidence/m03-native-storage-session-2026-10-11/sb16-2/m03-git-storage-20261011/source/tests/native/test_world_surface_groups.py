"""Real C clients exercise worldd relationships and authenticated application leases.

Actors use the explicit management fixture. This suite does not certify actor grants.
"""
import os
from pathlib import Path
import struct
import time
import unittest
from unittest.mock import patch
from harness.oracles import pose
from harness.probe import Probe
from harness.rpc import Client, RpcError
import security_pb2 as sec
import test_surface_identity as native


class WorldSurfaceGroupTests(unittest.TestCase):
    start = native.SurfaceIdentityTests.start
    connect = native.SurfaceIdentityTests.connect

    def setUp(self):
        native.SurfaceIdentityTests.setUp(self)
        self.supervisor = self.start()
        self.bin = native.binding.resources.native.PROBE.parent
        auth = self.auth(self.supervisor.issue(sec.COMPOSITOR, b'', b'').cookie, 'compositor')
        self.client = Client([str(self.bin/'worldd'), '--display', ':'+str(self.display)],
                             self.root/'world', self.root/'world-logs',
                             extra_env={'XAUTHORITY': str(auth), 'LIBGL_ALWAYS_SOFTWARE': '1'})
        self.addCleanup(self.stop)
        self.room = self.call('world.info')['entry_room']
        self.seq = {}
        self.probes = []
        name = b'WM_TRANSIENT_FOR'
        head, _ = self.supervisor.operation(16, self.supervisor.pack('HH', len(name), 0)+name, reply=True)
        self.transient_atom = struct.unpack_from('<I', head, 8)[0]

    def auth(self, cookie, name):
        data = b'\xff\xff'
        for part in (b'', str(self.display).encode(), b'MIT-MAGIC-COOKIE-1', cookie):
            data += struct.pack('!H', len(part))+part
        path = self.root/(name+'.auth'); path.write_bytes(data); path.chmod(0o600)
        return path

    def stop(self):
        try:
            if self.client.proc.poll() is None:
                self.client.proc.stdin.close()
                self.assertEqual(self.client.proc.wait(timeout=10), 0)
        finally:
            self.client.stop()
        for path in (self.root/'world-logs').glob('stderr-*'):
            text = path.read_text(errors='replace')
            for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'):
                self.assertNotIn(marker, text)
            if os.environ.get('SPATIAL_SECURITY_EVIDENCE'):
                target = Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])
                target.mkdir(parents=True, exist_ok=True)
                (target/(self._testMethodName+'-worldd.log')).write_text(text)

    def call(self, method, **args):
        return self.client.call(method, args)

    def until(self, fn, predicate):
        deadline = time.monotonic()+5
        while True:
            value = fn()
            if predicate(value): return value
            self.assertLess(time.monotonic(), deadline, str(value))
            time.sleep(.01)

    def probe(self, workspace=b'one', application=b'editor', position=(0, 0, -2)):
        grant = self.supervisor.issue(workspace=workspace, application=application)
        auth = self.auth(grant.cookie, 'probe-'+str(len(self.probes)))
        with patch.dict(os.environ, XAUTHORITY=str(auth), SPATIAL_XPROBE=str(self.bin/'spatial-xprobe')):
            probe = Probe(self.root, display=':'+str(self.display))
        self.addCleanup(probe.close)
        owner = self.connect(grant.cookie)
        surface = self.until(lambda: self.call('surface.find', probe_id=probe.id),
                             lambda reply: reply['surface'] is not None)['surface']
        self.call('surface.place', id=surface['id'], room=self.room, pose=pose(position), size=[2, 2])
        self.probes.append((probe, surface, owner))
        return probe, surface, owner

    def relation(self, child, parent):
        probe, _, owner = child
        if parent is None:
            result = owner.operation(19, owner.pack('II', probe.xid, self.transient_atom))
        else:
            result = owner.operation(18, owner.pack('IIIB3xII', probe.xid, self.transient_atom, 33, 32, 1, parent[0].xid))
        self.assertIsNone(result, 'Application could not set its own relationship hint')
        self.call('runtime.fence')
        return self.call('surface.get', id=child[1]['id'])

    def hidden(self, child, parent):
        before = self.call('surface.get', id=parent[1]['id'])
        value = self.relation(child, parent)
        self.assertFalse(value['mapped'])
        self.assertFalse(value.get('room'))
        self.assertFalse(value.get('parent_id'))
        children = self.call('surface.children', id=parent[1]['id'])['surfaces']
        self.assertNotIn(child[1]['id'], [s['id'] for s in children])
        self.assertEqual(self.call('surface.get', id=parent[1]['id']), before)

    def actor(self, name):
        actor = self.call('actor.open', id=name, grants=['interact'])
        self.seq[actor['token']] = 0
        return actor

    def act(self, actor, op, **args):
        self.seq[actor['token']] += 1
        return self.call('actor.act', token=actor['token'], seq=self.seq[actor['token']], op=op, args=args)

    def focus(self, actor, target, direction=(0, 0, -1)):
        hit = self.act(actor, 'aim', direction=list(direction))['hit']
        self.assertEqual(hit['id'], target[1]['id'])
        self.act(actor, 'pointer', button=1, pressed=True)
        self.act(actor, 'pointer', button=1, pressed=False)

    def key_events(self, target):
        self.call('runtime.fence')
        return [event['pressed'] for event in target[0].input_events() if event['event'] == 'key']

    def test_cross_workspace_hint_cannot_inherit_placement(self):
        parent = self.probe(); child = self.probe(workspace=b'two')
        self.hidden(child, parent)

    def test_cross_application_hint_cannot_inherit_placement(self):
        parent = self.probe(); child = self.probe(application=b'terminal')
        self.hidden(child, parent)

    def test_same_application_connections_can_form_relationship(self):
        parent = self.probe(); child = self.probe(position=(4, 0, -2))
        value = self.relation(child, parent)
        self.assertTrue(value['mapped']); self.assertEqual(value['room'], self.room)
        self.assertEqual(value['parent_id'], parent[1]['id'])
        self.assertEqual([s['id'] for s in self.call('surface.children', id=parent[1]['id'])['surfaces']], [child[1]['id']])

    def test_invalid_hint_does_not_break_other_scene_surfaces(self):
        parent = self.probe(); child = self.probe(application=b'other')
        self.hidden(child, parent)
        actor = self.actor('a')
        self.assertEqual(self.act(actor, 'aim', direction=[0, 0, -1])['hit']['id'], parent[1]['id'])

    def test_repaired_hint_restores_independent_placement(self):
        parent = self.probe(); child = self.probe(workspace=b'two', position=(4, 0, -2))
        self.hidden(child, parent)
        repaired = self.relation(child, None)
        self.assertTrue(repaired['mapped']); self.assertFalse(repaired.get('parent_id'))
        self.assertEqual(repaired['pose'], pose([4, 0, -2]))

    def test_same_group_reparent_preserves_held_key_and_focus(self):
        parent = self.probe(position=(4, 0, -2)); child = self.probe()
        actor = self.actor('a'); self.focus(actor, child)
        self.act(actor, 'key', keysym='a', pressed=True)
        before = self.call('actor.inspect', token=actor['token'])
        self.assertTrue(before['held_keys'])
        self.assertEqual(self.key_events(child), [True])
        self.relation(child, parent)
        after = self.call('actor.inspect', token=actor['token'])
        self.assertIsNotNone(after['focus'], 'Same-application hint change canceled the lease')
        self.assertEqual(after['focus']['id'], child[1]['id'])
        self.assertEqual(after['held_keys'], before['held_keys'])
        self.assertEqual(self.key_events(child), [True])
        self.act(actor, 'key', keysym='a', pressed=False)
        self.assertEqual(self.key_events(child), [True, False])

    def test_cross_application_reparent_releases_input(self):
        parent = self.probe(application=b'other', position=(4, 0, -2)); child = self.probe()
        actor = self.actor('a'); self.focus(actor, child)
        self.act(actor, 'key', keysym='a', pressed=True)
        self.assertTrue(self.call('actor.inspect', token=actor['token'])['held_keys'])
        self.hidden(child, parent)
        after = self.call('actor.inspect', token=actor['token'])
        self.assertFalse(after['held_keys']); self.assertFalse(after.get('focus'))
        self.assertEqual(self.key_events(child), [True, False])

    def test_same_application_detach_preserves_held_input(self):
        parent = self.probe(); child = self.probe()
        self.relation(child, parent)
        actor = self.actor('a'); self.focus(actor, child)
        self.act(actor, 'key', keysym='a', pressed=True)
        self.relation(child, None)
        state = self.call('actor.inspect', token=actor['token'])
        self.assertIsNotNone(state['focus']); self.assertEqual(state['focus']['id'], child[1]['id'])
        self.assertTrue(state['held_keys']); self.assertEqual(self.key_events(child), [True])

    def test_cross_application_grandparent_hides_descendants(self):
        parent = self.probe(); child = self.probe(application=b'other')
        grandchild = self.probe(application=b'other')
        self.relation(grandchild, child); self.hidden(child, parent)
        value = self.call('surface.get', id=grandchild[1]['id'])
        self.assertFalse(value['mapped']); self.assertFalse(value.get('room'))

    def test_cyclic_same_application_hints_remain_unplaced(self):
        parent = self.probe(); child = self.probe()
        self.relation(child, parent); self.relation(parent, child)
        for target in (parent, child):
            value = self.call('surface.get', id=target[1]['id'])
            self.assertFalse(value['mapped']); self.assertFalse(value.get('room'))

    def test_destroyed_parent_releases_descendant_input(self):
        parent = self.probe(); child = self.probe()
        self.relation(child, parent)
        actor = self.actor('a'); self.focus(actor, child)
        self.act(actor, 'key', keysym='a', pressed=True)
        parent[0].command('destroy'); self.call('runtime.fence')
        state = self.call('actor.inspect', token=actor['token'])
        self.assertFalse(state['held_keys']); self.assertFalse(state['focus'])
        self.assertEqual(self.key_events(child), [True, False])

    def test_same_application_top_levels_exclude_second_actor(self):
        first = self.probe(); second = self.probe(position=(2, 0, -2))
        a = self.actor('a'); b = self.actor('b'); self.focus(a, first)
        self.assertEqual(self.act(b, 'aim', direction=[2, 0, -2])['hit']['id'], second[1]['id'])
        with self.assertRaises(RpcError) as caught: self.act(b, 'pointer', button=1, pressed=True)
        self.assertEqual(caught.exception.code, 'BUSY')
        self.assertEqual(self.call('actor.inspect', token=a['token'])['focus']['id'], first[1]['id'])
        self.call('actor.close', token=a['token'])
        self.focus(b, second, (2, 0, -2))

    def test_distinct_applications_allow_simultaneous_control(self):
        first = self.probe(); second = self.probe(application=b'other', position=(2, 0, -2))
        a = self.actor('a'); b = self.actor('b')
        self.focus(a, first); self.focus(b, second, (2, 0, -2))
        self.assertEqual(self.call('actor.inspect', token=a['token'])['focus']['id'], first[1]['id'])
        self.assertEqual(self.call('actor.inspect', token=b['token'])['focus']['id'], second[1]['id'])

    def test_equal_application_names_in_distinct_workspaces_have_separate_leases(self):
        first = self.probe(); second = self.probe(workspace=b'two', position=(2, 0, -2))
        a = self.actor('a'); b = self.actor('b')
        self.focus(a, first); self.focus(b, second, (2, 0, -2))
        self.assertEqual(self.call('actor.inspect', token=a['token'])['focus']['id'], first[1]['id'])
        self.assertEqual(self.call('actor.inspect', token=b['token'])['focus']['id'], second[1]['id'])


if __name__ == '__main__': unittest.main(verbosity=2)
