"""Object drafts use the live renderer and production revision-checked storage."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from harness.oracles import PPM, pose
from harness.rpc import Client, RpcError
from harness.probe import Probe
from harness.wire import pb

HOST, DAEMON, PROBE = sys.argv[1:4]
sys.argv[1:4] = []
os.environ['SPATIAL_XPROBE'] = PROBE
DISPLAY = os.environ['SPATIAL_TEST_DISPLAY']


class ObjectTests(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get('SPATIAL_RENDER_EVIDENCE')
        if retained:
            self.root = Path(retained)/self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.seq = {}; self.start(); self.room = self.call('world.info')['entry_room']

    def start(self):
        self.client = Client([HOST], self.root/'state', self.root/f'logs-{time.monotonic_ns()}', timeout=8)
        self.addCleanup(self.client.stop)

    def call(self, method, **args):
        return self.client.call(method, args)

    def actor(self, ident='a', grants=None, placement=None):
        actor = self.call('actor.open', id=ident, pose=placement or pose(),
                          grants=['world.write','navigate','interact'] if grants is None else grants)
        self.seq[actor['token']] = 0; return actor

    def act(self, actor, op, **args):
        self.seq[actor['token']] += 1
        return self.call('actor.act', token=actor['token'], seq=self.seq[actor['token']], op=op, args=args)

    def denied(self, code, actor, op, **args):
        with self.assertRaises(RpcError) as caught: self.act(actor, op, **args)
        self.assertEqual(caught.exception.code, code)

    def create(self, ident='object', placement=None, kind='object', room=None, **fields):
        return self.call('world.create', entity=dict(id=ident, kind=kind, room=room or self.room,
                         pose=placement or pose([0,0,-1]), **fields))

    def visible(self, actor):
        return {item['id']:item for item in self.act(actor,'observe')['visible']}

    def draft(self, actor, point=(.5,0,-1)):
        self.act(actor,'object.grab',id='object'); self.act(actor,'object.move',pose=pose(point))

    def test_preview_is_shared_and_visible_but_only_explicit_commit_is_durable(self):
        self.create(); a,b=self.actor(),self.actor('b'); before=self.call('world.snapshot')
        self.draft(a)
        for actor in [a,b]:
            item=self.visible(actor)['object']; self.assertEqual(item['pose'],pose([.5,0,-1]))
            self.assertEqual(item['kind'],'object'); self.assertIsNone(item['pixels'])
            hit=self.act(actor,'aim',direction=[.5,0,-1])['hit']
            self.assertEqual(hit['id'],'object'); self.assertIsNone(hit['pixel'])
        frame=self.call('render.capture',token=b['token'],width=128,height=128)
        self.assertEqual(PPM(frame['path']).pixel(96,64),(224,174,72))
        self.assertEqual(self.call('world.snapshot'),before)
        self.assertEqual(self.call('world.get',id='object')['pose'],pose([0,0,-1]))
        self.call('world.checkpoint'); self.assertEqual(self.call('world.get',id='object')['pose'],pose([0,0,-1]))
        self.act(a,'object.release',commit=True)
        saved=self.call('world.get',id='object'); self.assertEqual(saved['pose'],pose([.5,0,-1]))
        self.assertEqual(saved['revision'],2); self.assertEqual(saved['world_revision'],before['world_revision']+1)
        self.denied('NO_TARGET',a,'object.release',commit=True)

    def test_cancel_and_repeated_cancel_discard_preview_without_writes(self):
        self.create(); a=self.actor(); before=self.call('world.snapshot'); self.draft(a)
        self.act(a,'object.release',commit=False); self.act(a,'object.release',commit=False)
        self.assertEqual(self.call('world.snapshot'),before)
        self.assertEqual(self.visible(a)['object']['pose'],pose([0,0,-1]))
        self.act(a,'object.grab',id='object')

    def test_stop_discards_preview_and_preserves_another_object_lease(self):
        self.create(); self.create('second',pose([.5,0,-1])); a,b=self.actor(),self.actor('b')
        self.act(a,'object.grab',id='object'); self.act(b,'object.grab',id='second')
        self.act(a,'object.move',pose=pose([-.5,0,-1])); self.act(b,'object.move',pose=pose([1,0,-2]))
        self.act(a,'stop'); self.denied('NO_TARGET',a,'object.move',pose=pose())
        self.act(b,'object.release',commit=True)
        self.assertEqual(self.call('world.get',id='object')['pose'],pose([0,0,-1]))
        self.assertEqual(self.call('world.get',id='second')['pose'],pose([1,0,-2]))

    def test_one_actor_cannot_acquire_a_second_object_implicitly(self):
        self.create(); self.create('second',pose([.5,0,-1])); a=self.actor()
        self.act(a,'object.grab',id='object'); self.denied('BUSY',a,'object.grab',id='second')
        self.act(a,'object.move',pose=pose([-.5,0,-1])); self.act(a,'object.release',commit=True)
        self.assertEqual(self.call('world.get',id='second')['revision'],1)

    def test_close_discards_preview_and_releases_lease(self):
        self.create(); a,b=self.actor(),self.actor('b'); self.draft(a)
        self.call('actor.close',token=a['token']); self.act(b,'object.grab',id='object')
        self.assertEqual(self.visible(b)['object']['pose'],pose([0,0,-1]))
        self.denied('UNAUTHORIZED',a,'object.move',pose=pose())

    def test_crash_does_not_persist_preview_or_lease(self):
        self.create(); a=self.actor(); self.draft(a); self.call('world.checkpoint')
        self.client.stop(crash=True); self.start(); b=self.actor('b')
        self.denied('UNAUTHORIZED',a,'object.release',commit=True)
        self.assertEqual(self.call('world.get',id='object')['pose'],pose([0,0,-1]))
        self.act(b,'object.grab',id='object')

    def test_reach_uses_center_and_includes_exact_boundary(self):
        self.create(placement=pose([0,0,-3])); a=self.actor()
        self.act(a,'object.grab',id='object'); self.act(a,'object.release',commit=False)
        self.call('world.update',id='object',expected_revision=1,patch=dict(pose=pose([0,0,-3.00001])))
        self.denied('OUT_OF_REACH',a,'object.grab',id='object')

    def test_held_preview_can_move_beyond_reach(self):
        self.create(); a=self.actor(); self.draft(a,[8,4,-30]); self.act(a,'object.release',commit=True)
        self.assertEqual(self.call('world.get',id='object')['pose'],pose([8,4,-30]))
        self.denied('OUT_OF_REACH',a,'object.grab',id='object')

    def test_other_room_and_outside_view_are_not_visible(self):
        a=self.actor(); self.call('world.create',entity=dict(id='other',kind='room',label='Other'))
        self.create(room='other'); self.denied('NOT_VISIBLE',a,'object.grab',id='object')
        for i,point in enumerate([[0,0,1],[2,0,-1]]):
            ident=f'offscreen-{i}'; self.create(ident,pose(point)); self.denied('NOT_VISIBLE',a,'object.grab',id=ident)

    def test_panel_occlusion_denies_grab_without_side_effects(self):
        self.create(); a=self.actor()
        self.create('cover',pose([0,0,-.5]),kind='panel',size=[1,1],color=[20,30,40],label='Cover')
        before=self.call('world.snapshot'); self.denied('NOT_VISIBLE',a,'object.grab',id='object')
        self.assertEqual(self.call('world.snapshot'),before); self.assertNotIn('object',self.visible(a))

    def test_barrier_occlusion_denies_grab(self):
        self.create(); a=self.actor()
        self.create('wall',pose(),kind='barrier',bounds=dict(min=[-1,-1,-.6],max=[1,1,-.5]))
        self.denied('NOT_VISIBLE',a,'object.grab',id='object')

    def test_visible_fragment_permits_grab_when_center_ray_is_occluded(self):
        self.create(); a=self.actor()
        self.create('cover',pose([0,0,-.5]),kind='panel',size=[.05,1],color=[20,30,40],label='Cover')
        self.assertEqual(self.act(a,'aim',direction=[0,0,-1])['hit']['id'],'cover')
        self.assertIn('object',self.visible(a)); self.act(a,'object.grab',id='object')

    def test_edit_conflict_discards_draft_and_never_overwrites_new_data(self):
        self.create(); a,b=self.actor(),self.actor('b'); self.draft(a)
        latest=self.call('world.update',id='object',expected_revision=1,patch=dict(pose=pose([-.5,0,-1])))
        self.assertEqual(self.visible(b)['object']['pose'],latest['pose'])
        self.denied('STALE_TARGET',a,'object.release',commit=True)
        self.assertEqual(self.call('world.get',id='object'),latest); self.act(b,'object.grab',id='object')

    def test_delete_and_recreate_cannot_reuse_an_old_lease(self):
        self.create(); a,b=self.actor(),self.actor('b'); self.draft(a)
        self.call('world.delete',id='object',expected_revision=1); new=self.create(placement=pose([-.5,0,-1]))
        self.assertEqual(new['revision'],1); self.denied('STALE_TARGET',a,'object.release',commit=True)
        self.assertEqual(self.call('world.get',id='object'),new); self.act(b,'object.grab',id='object')

    def test_storage_failure_retains_draft_for_explicit_retry(self):
        self.create(); a,b=self.actor(),self.actor('b'); before=self.call('world.snapshot'); self.draft(a)
        with sqlite3.connect(self.root/'state/world.db') as db:
            db.execute("CREATE TRIGGER reject_object BEFORE UPDATE ON entities WHEN old.id='object' BEGIN SELECT RAISE(ABORT,'object write failure'); END")
        self.denied('STORAGE_ERROR',a,'object.release',commit=True)
        self.assertEqual(self.call('world.snapshot'),before)
        self.assertEqual(self.visible(b)['object']['pose'],pose([.5,0,-1]))
        self.denied('BUSY',b,'object.grab',id='object')
        with sqlite3.connect(self.root/'state/world.db') as db: db.execute('DROP TRIGGER reject_object')
        self.act(a,'object.release',commit=True); self.assertEqual(self.call('world.get',id='object')['revision'],2)

    def test_portal_crossing_cancels_preview_in_source_room(self):
        self.create(); a,b=self.actor(),self.actor('b'); self.draft(a)
        self.call('world.create',entity=dict(id='other',kind='room',label='Other'))
        self.create('door',pose([0,0,-.1]),kind='portal',size=[2,2],destination=dict(room='other',pose=pose([3,0,0])))
        self.act(a,'input',axes=[0,0,-1,0,0,0]); self.call('clock.advance',seconds=.2)
        self.assertEqual(self.call('actor.inspect',token=a['token'])['room'],'other')
        self.denied('NO_TARGET',a,'object.release',commit=True)
        self.assertEqual(self.call('world.get',id='object')['pose'],pose([0,0,-1])); self.act(b,'object.grab',id='object')

    def test_permission_checks_precede_target_and_consume_sequence(self):
        self.create(); a=self.actor(grants=['navigate'])
        for op,args in [('object.grab',dict(id='missing')),('object.move',{}),('object.release',{})]:
            self.denied('FORBIDDEN',a,op,**args)
            with self.assertRaises(RpcError) as caught:
                self.call('actor.act',token=a['token'],seq=self.seq[a['token']],op='stop',args={})
            self.assertEqual(caught.exception.code,'STALE_SEQUENCE')
        self.assertEqual(self.call('world.get',id='object')['revision'],1)

    def test_missing_fields_and_invalid_pose_do_not_change_held_draft(self):
        self.create(); a=self.actor(); self.draft(a)
        for op in ['object.grab','object.move','object.release']: self.denied('INVALID_ARGUMENT',a,op)
        self.denied('INVALID_ARGUMENT',a,'object.move',pose=dict(p=[0,0,-2],q=[0,0,0,2]))
        self.assertEqual(self.visible(a)['object']['pose'],pose([.5,0,-1]))
        self.act(a,'object.release',commit=True); self.assertEqual(self.call('world.get',id='object')['pose'],pose([.5,0,-1]))

    def test_unknown_object_wire_fields_are_rejected(self):
        a=self.actor(); request=pb.Request(protocol_version=1)
        request.actor_act.token=a['token']; request.actor_act.seq=1
        request.actor_act.object_release.ParseFromString(b'\x08\x00\x10\x01')
        with self.assertRaises(RpcError) as caught: self.client.call_message(request)
        self.assertEqual(caught.exception.code,'INVALID_ARGUMENT')

    def test_native_surface_occludes_object_and_object_never_injects_input(self):
        self.create(); a=self.actor(); probe=Probe(self.root,xi2=True,display=DISPLAY); self.addCleanup(probe.close)
        deadline=time.monotonic()+4
        while True:
            surface=self.call('surface.find',probe_id=probe.id)['surface']
            if surface: break
            if time.monotonic()>deadline: self.fail('Native surface did not appear')
            time.sleep(.005)
        self.call('surface.place',id=surface['id'],room=self.room,pose=pose([0,0,-.5]),size=[1,1])
        self.denied('NOT_VISIBLE',a,'object.grab',id='object')
        self.call('surface.place',id=surface['id'],room=self.room,pose=pose([0,0,-2]),size=[1,1])
        self.act(a,'object.grab',id='object'); self.assertEqual(self.act(a,'aim',direction=[0,0,-1])['hit']['id'],'object')
        self.denied('NO_TARGET',a,'pointer',button=1,pressed=True); self.call('runtime.fence')
        self.assertEqual([e for e in probe.input_events() if e['event'] in ['button','key']],[])


if __name__ == '__main__': unittest.main(verbosity=2)
