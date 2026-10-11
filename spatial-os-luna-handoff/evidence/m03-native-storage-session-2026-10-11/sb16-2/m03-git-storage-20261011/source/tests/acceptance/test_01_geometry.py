import math
import random
from harness.base import Acceptance, case
from harness.oracles import pose, axis_angle, transform, uv_pixel

class GeometryTests(Acceptance):
    @case("P01","geometry.identity")
    def test_identity(self):
        self.assertVec(self.call("geometry.transform_point",pose=pose(),point=[3,-4,7])["point"],[3,-4,7])
    @case("P01","geometry.translation")
    def test_translation_not_direction(self):
        self.assertVec(self.call("geometry.transform_point",pose=pose([5,6,7]),point=[1,2,3])["point"],[6,8,10])
    @case("P01","geometry.quaternion_order")
    def test_yaw_turns_forward_to_left(self):
        p=pose(q=axis_angle([0,1,0],math.pi/2))
        self.assertVec(self.call("geometry.transform_point",pose=p,point=[0,0,-1])["point"],[-1,0,0])
    @case("P01","geometry.inverse")
    def test_inverse_known_and_seeded_cases(self):
        rng=random.Random(1907)
        for _ in range(50):
            axis=[rng.uniform(-1,1) for _ in range(3)]
            p=pose([rng.uniform(-10,10) for _ in range(3)],axis_angle(axis,rng.uniform(-math.pi,math.pi)))
            local=[rng.uniform(-4,4) for _ in range(3)]
            world=transform(p,local)
            with self.subTest(p=p,local=local):
                self.assertVec(self.call("geometry.transform_point",pose=p,point=local)["point"],world)
                self.assertVec(self.call("geometry.inverse_point",pose=p,point=world)["point"],local)
    @case("P01","geometry.quaternion_sign")
    def test_q_and_negative_q_equivalent(self):
        q=axis_angle([1,2,3],.7)
        a=self.call("geometry.transform_point",pose=pose(q=q),point=[7,8,9])
        b=self.call("geometry.transform_point",pose=pose(q=[-x for x in q]),point=[7,8,9])
        self.assertVec(a["point"],b["point"])
    @case("P01","geometry.invalid_pose")
    def test_zero_and_nonunit_quaternion_rejected(self):
        for q in ([0,0,0,0],[0,0,0,2]):
            with self.subTest(q=q):
                self.error("INVALID_ARGUMENT","geometry.transform_point",pose=pose(q=q),point=[0,0,0])
    @case("P01","geometry.ray_center")
    def test_center_ray_returns_top_left_uv_convention(self):
        r=self.call("geometry.ray_panel",origin=[0,0,0],direction=[0,0,-1],
                    pose=pose([0,0,-4]),size=[2,2],pixels=[320,240])["hit"]
        self.assertVec(r["uv"],[.5,.5]); self.assertEqual(r["pixel"],[160,120]); self.assertAlmostEqual(r["distance"],4)
    @case("P01","geometry.ray_rotated")
    def test_rotated_panel_uses_its_local_frame(self):
        p=pose([1,2,-4],axis_angle([0,1,0],.4))
        point=transform(p,[.5,.5,0])
        length=math.sqrt(sum(v*v for v in point))
        r=self.call("geometry.ray_panel",origin=[0,0,0],direction=[v/length for v in point],pose=p,size=[2,2],pixels=[320,240])["hit"]
        self.assertVec(r["uv"],[.75,.25]); self.assertEqual(r["pixel"],[240,60])
    @case("P01","geometry.ray_miss")
    def test_parallel_behind_backface_and_outside_are_misses(self):
        for origin,direction in [([0,0,0],[1,0,0]),([0,0,0],[0,0,1]),([5,0,0],[0,0,-1]),([0,0,-5],[0,0,1])]:
            with self.subTest(origin=origin,direction=direction):
                self.assertIsNone(self.call("geometry.ray_panel",origin=origin,direction=direction,pose=pose([0,0,-4]),size=[2,2],pixels=[320,240])["hit"])
    @case("P01","geometry.ray_validation")
    def test_zero_direction_and_invalid_extents(self):
        for direction,size,pixels in [([0,0,0],[2,2],[320,240]),([0,0,-1],[0,2],[320,240]),([0,0,-1],[2,2],[0,240])]:
            self.error("INVALID_ARGUMENT","geometry.ray_panel",origin=[0,0,0],direction=direction,pose=pose([0,0,-4]),size=size,pixels=pixels)
    @case("P01","geometry.half_open_pixels")
    def test_uv_is_half_open_and_never_clamped_into_target(self):
        for uv in ([0,0],[.5,.5],[.999999,.999999],[-1e-9,.5],[1,.5],[.5,1]):
            with self.subTest(uv=uv):
                self.assertEqual(self.call("geometry.uv_pixel",uv=uv,pixels=[320,240])["pixel"],uv_pixel(uv,[320,240]))
    @case("P01","motion.se3_constant_twist")
    def test_simultaneous_turn_and_forward_follow_arc(self):
        r=self.call("motion.integrate",pose=pose(),linear=[0,0,-1],angular=[0,math.pi/2,0],seconds=1)
        self.assertPose(r["pose"],pose([-2/math.pi,0,-2/math.pi],axis_angle([0,1,0],math.pi/2)))
    @case("P01","motion.partition_invariance")
    def test_same_twist_same_result_at_different_tick_rates(self):
        initial=pose([1,-2,3],axis_angle([2,1,3],.4))
        args={"linear":[.3,-.7,1.2],"angular":[.4,.2,-.5]}
        one=self.call("motion.integrate",pose=initial,seconds=1,**args)["pose"]
        for n in (10,30,60,144):
            p=initial
            for _ in range(n):
                p=self.call("motion.integrate",pose=p,seconds=1/n,**args)["pose"]
            with self.subTest(n=n): self.assertPose(p,one,2e-5)
    @case("P01","motion.invalid_time")
    def test_negative_time_rejected(self):
        self.error("INVALID_ARGUMENT","motion.integrate",pose=pose(),linear=[0,0,0],angular=[0,0,0],seconds=-1)
    @case("P01","motion.zero_time")
    def test_zero_time_is_identity(self):
        p=pose([1,2,3],axis_angle([1,2,3],.8))
        self.assertPose(self.call("motion.integrate",pose=p,linear=[1,2,3],angular=[1,2,3],seconds=0)["pose"],p)

def _axis_test(index, sign, rotation):
    def run(self):
        v=[0.,0.,0.]; v[index]=sign
        result=self.call("motion.integrate",pose=pose(),linear=[0,0,0] if rotation else v,
                         angular=v if rotation else [0,0,0],seconds=.25)["pose"]
        expected=pose(q=axis_angle(v,.25)) if rotation else pose([x*.25 for x in v])
        self.assertPose(result,expected)
    return case("P01","motion.six_signed_axes")(run)
for rotational in (False,True):
    for axis in range(3):
        for sign in (-1,1):
            setattr(GeometryTests,f"test_axis_{'r' if rotational else 't'}{axis}_{'neg' if sign<0 else 'pos'}",_axis_test(axis,sign,rotational))
