"""Independent analytical and numerical witnesses for collision and visible barriers."""
from collections import Counter
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import time
import unittest
from harness.oracles import PPM, pose, axis_angle
from harness.rpc import Client, RpcError
from harness.probe import Probe
from harness.wire import pb

HOST, DAEMON, PROBE = sys.argv[1:4]
sys.argv[1:4] = []
os.environ['SPATIAL_XPROBE'] = PROBE
DISPLAY = os.environ['SPATIAL_TEST_DISPLAY']
BOX = dict(min=[0, 0, 0], max=[1, 1, 1])


class CollisionTests(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get('SPATIAL_RENDER_EVIDENCE')
        if retained:
            self.root = Path(retained)/self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
            self.root = Path(temp.name)
        self.sequence = {}

    def start(self, linear=1, angular=1, radius=.2):
        config = self.root/'collision.textproto'
        config.write_text(f'linear_gain: {linear} angular_gain: {angular} actor_radius: {radius} '
                          'deadzone: 0.05 input_timeout: 0.25 '
                          'render { vertical_fov: 1.5707963267948966 near_clip: 0.01 far_clip: 1000 '
                          'background { red: 10 green: 12 blue: 16 } }')
        self.argv = [DAEMON, '--fixture-clock', '--display', DISPLAY, '--config', str(config)]
        self.client = Client(self.argv, self.root/'state', self.root/'logs', timeout=8)
        self.addCleanup(self.client.stop)
        self.room = self.call('world.info')['entry_room']

    def call(self, method, **params):
        return self.client.call(method, params)

    def sweep(self, origin, displacement, radius=.5, bounds=BOX):
        return self.call('geometry.sweep_sphere_aabb', origin=origin, displacement=displacement, radius=radius, bounds=bounds)['hit']

    def vec(self, actual, expected, tolerance=1e-8):
        for x, y in zip(actual, expected):
            self.assertAlmostEqual(x, y, delta=tolerance)

    def actor(self, ident='actor', placement=None, room=None):
        actor = self.call('actor.open', id=ident, room=room or self.room, pose=placement or pose(), grants=['navigate'])
        self.sequence[actor['token']] = 0
        return actor

    def act(self, actor, op, **args):
        self.sequence[actor['token']] += 1
        return self.call('actor.act', token=actor['token'], seq=self.sequence[actor['token']], op=op, args=args)

    def position(self, actor):
        return self.call('actor.inspect', token=actor['token'])['pose']['p']

    def move(self, actor, axes, seconds):
        self.act(actor, 'input', axes=axes)
        self.call('clock.advance', seconds=seconds)
        return self.position(actor)

    def barrier(self, ident='wall', low=(.3, -3, -3), high=(.31, 3, 3), placement=None, room=None):
        return self.call('world.create', entity=dict(id=ident, kind='barrier', room=room or self.room,
                         pose=placement or pose(), bounds=dict(min=list(low), max=list(high))))

    def test_each_face_and_endpoint_contact(self):
        self.start()
        for axis in range(3):
            for sign in [-1, 1]:
                origin = [.5]*3; origin[axis] = -1 if sign < 0 else 2
                displacement = [0]*3; displacement[axis] = -sign * 2
                hit = self.sweep(origin, displacement)
                self.assertAlmostEqual(hit['fraction'], .25)
                normal = [0]*3; normal[axis] = sign
                self.vec(hit['normal'], normal)
                expected = origin[:]; expected[axis] += displacement[axis]*.25
                self.vec(hit['position'], expected)
        hit = self.sweep([-1, .5, .5], [.5, 0, 0])
        self.assertEqual(hit['fraction'], 1)
        self.vec(hit['position'], [-.5, .5, .5])

    def test_rounded_edge_and_corner_contacts(self):
        self.start()
        hit = self.sweep([-1, 1.3, .5], [2, 0, 0])
        self.assertAlmostEqual(hit['fraction'], .3)
        self.vec(hit['normal'], [-.8, .6, 0])
        x = math.sqrt(.6**2 - .3**2 - .4**2)
        hit = self.sweep([-1, 1.3, 1.4], [2, 0, 0], radius=.6)
        self.assertAlmostEqual(hit['fraction'], (1-x)/2)
        self.vec(hit['normal'], [-x/.6, .3/.6, .4/.6])

    def test_false_box_contacts_and_grazing(self):
        self.start()
        self.assertIsNone(self.sweep([-1, 1.4, 1.4], [3, 0, 0]))
        self.assertIsNone(self.sweep([-.4, 1.4, 1.4], [0, 0, 0]))
        tangent = self.sweep([-1, 1.5, .5], [3, 0, 0])
        self.assertAlmostEqual(tangent['fraction'], 1/3)
        self.vec(tangent['normal'], [0, 1, 0])
        self.assertIsNone(self.sweep([-1, 1.5000001, .5], [3, 0, 0]))

    def test_initial_overlap_normal_is_deterministic(self):
        self.start()
        for point, normal in [([.5, .5, .5], [-1, 0, 0]), ([.9, .5, .5], [1, 0, 0]),
                              ([.5, 1.25, .5], [0, 1, 0]), ([1.2, 1.2, .5], [math.sqrt(.5), math.sqrt(.5), 0])]:
            hit = self.sweep(point, [0, 0, 0])
            self.assertEqual(hit['fraction'], 0)
            self.vec(hit['position'], point); self.vec(hit['normal'], normal)
        self.assertIsNone(self.sweep([3, 3, 3], [0, 0, 0]))

    def test_scale_range_and_reversed_segment(self):
        self.start()
        for scale in [1e-100, 1, 1e100]:
            bounds = dict(min=[0, 0, 0], max=[scale]*3)
            hit = self.sweep([-scale, .5*scale, .5*scale], [2*scale, 0, 0], radius=.2*scale, bounds=bounds)
            self.assertAlmostEqual(hit['fraction'], .4, delta=1e-12)
            self.vec(hit['normal'], [-1, 0, 0])
            self.assertAlmostEqual(hit['position'][0]/scale, -.2, delta=1e-12)
            self.assertIsNone(self.sweep([-scale, .5*scale, .5*scale], [-2*scale, 0, 0], radius=.2*scale, bounds=bounds))

    def test_random_sweeps_match_independent_convex_distance_search(self):
        self.start(); rng = random.Random(781923)
        for _ in range(180):
            origin = [rng.uniform(-3, 3) for _ in range(3)]
            displacement = [rng.uniform(-5, 5) for _ in range(3)]
            radius = rng.uniform(.03, .8)
            def distance(t):
                point = [o+t*d for o,d in zip(origin, displacement)]
                return math.sqrt(sum((p-min(1,max(0,p)))**2 for p in point))
            low, high = 0., 1.
            for __ in range(100):
                a, b = (2*low+high)/3, (low+2*high)/3
                if distance(a) < distance(b): high = b
                else: low = a
            closest = (low+high)/2
            hit = self.sweep(origin, displacement, radius)
            if distance(0) <= radius:
                self.assertIsNotNone(hit); self.assertEqual(hit['fraction'], 0)
            elif distance(closest) > radius + 1e-10:
                self.assertIsNone(hit)
            else:
                self.assertIsNotNone(hit)
                low, high = 0., closest
                for __ in range(70):
                    middle = (low+high)/2
                    if distance(middle) <= radius: high = middle
                    else: low = middle
                self.assertAlmostEqual(hit['fraction'], high, delta=1e-8)
                self.assertAlmostEqual(distance(hit['fraction']), radius, delta=1e-8)
                self.assertAlmostEqual(math.hypot(*hit['normal']), 1, delta=1e-10)

    def test_wire_presence_and_invalid_geometry(self):
        self.start()
        request = pb.Request(protocol_version=1)
        request.sweep_sphere_aabb.origin.x = -1
        request.sweep_sphere_aabb.displacement.x = 2
        request.sweep_sphere_aabb.radius = .5
        request.sweep_sphere_aabb.bounds.min.SetInParent()
        request.sweep_sphere_aabb.bounds.max.CopyFrom(pb.Vec3(x=1,y=1,z=1))
        for field in ['origin', 'displacement', 'radius', 'bounds']:
            bad = pb.Request(); bad.CopyFrom(request); bad.sweep_sphere_aabb.ClearField(field)
            with self.assertRaises(RpcError) as caught: self.client.call_message(bad)
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        for radius, bounds in [(0,BOX),(-1,BOX),(.2,dict(min=[0,0,0],max=[0,1,1])),(.2,dict(min=[2,0,0],max=[1,1,1]))]:
            with self.assertRaises(RpcError) as caught: self.sweep([-1,0,0], [2,0,0], radius, bounds)
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.assertIsNotNone(self.sweep([-1,.5,.5], [2,0,0]))

    def test_thin_wall_fast_motion_uses_configured_sphere(self):
        self.start(linear=10, radius=.05); actor = self.actor(); self.barrier()
        self.vec(self.move(actor, [1,0,0,0,0,0], .25), [.25,0,0], 1e-8)
        self.call('clock.advance', seconds=1)
        self.vec(self.position(actor), [.25,0,0], 1e-8)

    def test_sliding_separating_motion_and_multiple_contacts(self):
        self.start(); actor = self.actor(); self.barrier()
        self.vec(self.move(actor, [1,1,0,0,0,0], .2), [.1,.2,0])
        for _ in range(10): self.move(actor, [0,1,0,0,0,0], .1)
        self.vec(self.position(actor), [.1,1.2,0])
        self.vec(self.move(actor, [-1,0,0,0,0,0], .1), [0,1.2,0])
        other = self.actor('corner')
        self.barrier('ceiling', low=(-3,.3,-3), high=(3,.31,3))
        self.vec(self.move(other, [1,1,0,0,0,0], .25), [.1,.1,0])

    def test_initial_overlap_separates_without_teleporting_along_input(self):
        self.start(); actor = self.actor(); self.barrier(low=(-.05,-.05,-.05), high=(.05,.05,.05))
        self.vec(self.move(actor, [0,1,0,0,0,0], .1), [-.25,.1,0])

    def test_room_filter_and_committed_geometry_updates(self):
        self.start(); actor = self.actor()
        other = self.call('world.create', entity=dict(id='elsewhere',kind='room',label='elsewhere'))
        self.barrier('foreign',room=other['id'])
        self.vec(self.move(actor,[1,0,0,0,0,0],.2),[.2,0,0])
        wall = self.barrier(low=(.5,-3,-3),high=(.51,3,3))
        self.vec(self.move(actor,[1,0,0,0,0,0],.2),[.3,0,0])
        wall = self.call('world.update', id=wall['id'],expected_revision=wall['revision'],patch=dict(bounds=dict(min=[1,-3,-3],max=[1.01,3,3])))
        self.vec(self.move(actor,[1,0,0,0,0,0],.2),[.5,0,0])
        self.call('world.delete',id=wall['id'],expected_revision=wall['revision'])
        self.vec(self.move(actor,[1,0,0,0,0,0],.2),[.7,0,0])

    def test_rotated_barrier_render_pick_observation_and_collision_agree(self):
        self.start(); actor = self.actor()
        placement = pose([0,0,0],axis_angle([0,1,0],math.pi/2))
        barrier = self.barrier(placement=placement)
        hit = self.act(actor,'aim',direction=[0,0,-1])['hit']
        self.assertEqual(hit['id'],barrier['id']);self.assertAlmostEqual(hit['distance'],.3)
        self.assertIsNone(hit['pixel'])
        observation = self.act(actor,'observe',width=128,height=128)
        item, = observation['visible']
        self.assertEqual(item['kind'],'barrier');self.assertEqual(item['pose'],placement)
        self.assertEqual(item['bounds'],barrier['bounds'])
        frame = self.call('render.capture',token=actor['token'],width=128,height=128)
        image = PPM(frame['path']);self.assertEqual(image.pixel(64,64),(96,104,120))
        self.vec(self.move(actor,[0,0,-1,0,0,0],.25),[0,0,-.1])
        self.assertAlmostEqual(self.act(actor,'aim',direction=[0,0,-1])['hit']['distance'],.2,delta=1e-8)
        back = self.actor('back',pose([0,0,-1],axis_angle([0,1,0],math.pi)))
        self.assertAlmostEqual(self.act(back,'aim',direction=[0,0,-1])['hit']['distance'],.69)

    def test_multiple_visible_faces_report_one_solid(self):
        self.start(); self.barrier(low=(0,0,0),high=(1,1,1))
        actor = self.actor(placement=pose([2,.5,2],axis_angle([0,1,0],math.pi/4)))
        observation = self.act(actor,'observe',width=127,height=95)
        item, = observation['visible']
        self.assertEqual(item['id'],'wall'); self.assertEqual(item['kind'],'barrier')
        frame = self.call('render.capture',token=actor['token'],width=127,height=95)
        image = PPM(frame['path']);counts=Counter(tuple(image.data[i:i+3]) for i in range(0,len(image.data),3))
        self.assertEqual(item['visible_pixels'],counts[(96,104,120)])
        self.assertEqual(self.act(actor,'aim',direction=item['direction'])['hit']['id'],'wall')

    def test_all_six_barrier_faces_render_and_pick(self):
        self.start(); self.barrier(low=(-.5,-.5,-.5),high=(.5,.5,.5))
        for axis in range(3):
            for sign in [-1,1]:
                point = [0,0,0]; point[axis] = 2*sign
                if axis == 0: rotation = axis_angle([0,1,0],sign*math.pi/2)
                elif axis == 1: rotation = axis_angle([1,0,0],-sign*math.pi/2)
                else: rotation = axis_angle([0,1,0],math.pi if sign < 0 else 0)
                actor = self.actor(f'view-{axis}-{sign}',pose(point,rotation))
                hit = self.act(actor,'aim',direction=[0,0,-1])['hit']
                self.assertEqual(hit['id'],'wall');self.assertAlmostEqual(hit['distance'],1.5)
                frame = self.call('render.capture',token=actor['token'],width=64,height=64)
                self.assertEqual(PPM(frame['path']).pixel(32,32),(96,104,120))

    def test_barrier_occludes_native_surface_and_clears_application_pixel(self):
        self.start(); actor = self.actor()
        probe = Probe(self.root,display=DISPLAY); self.addCleanup(probe.close)
        deadline = time.monotonic()+5
        while True:
            surface = self.call('surface.find',probe_id=probe.id)['surface']
            if surface: break
            if time.monotonic() >= deadline: self.fail('Native surface did not register')
            time.sleep(.005)
        self.call('surface.place',id=surface['id'],room=self.room,pose=pose([0,0,-4]),size=[2,2])
        self.assertEqual(self.act(actor,'aim',direction=[0,0,-1])['hit']['id'],surface['id'])
        self.barrier(low=(-1,-1,-3),high=(1,1,-2.9))
        hit = self.act(actor,'aim',direction=[0,0,-1])['hit']
        self.assertEqual(hit['id'],'wall');self.assertIsNone(hit['pixel'])
        self.assertEqual([v['id'] for v in self.act(actor,'observe')['visible']],['wall'])

    def test_body_twist_arc_cannot_skip_wall_when_endpoints_miss(self):
        self.start(angular=4*math.pi, radius=.005)
        actor = self.actor();self.barrier(low=(-2,-2,-.041),high=(2,2,-.04))
        result = self.move(actor,[0,0,-1,0,1,0],.25)
        self.assertAlmostEqual(result[0],-2/(4*math.pi),delta=2e-5)
        self.assertAlmostEqual(result[2],1/(4*math.pi)-.035,delta=2e-5)
        self.assertGreater(result[2],.04)


if __name__ == '__main__':
    unittest.main(verbosity=2)
