import math
from concurrent.futures import ThreadPoolExecutor
from harness.base import Acceptance,case
from harness.oracles import pose,axis_angle
from harness.rpc import RpcError

class RoomsObjectsTests(Acceptance):
    def setUp(self):
        super().setUp();self.room();self.a=self.actor()
    def portal(self):
        self.room("room-b")
        return self.obj("door",kind="portal",pose=pose([0,0,-.5]),size=[2,3],
                        destination={"room":"room-b","pose":pose([4,0,2])})
    def move(self,actor,axes,seconds):
        while seconds>1e-10:
            dt=min(.2,seconds);actor.act("input",axes=axes);self.advance(dt);seconds-=dt
        actor.act("stop")
    @case("P07","room.portal_crossing")
    def test_walking_through_portal_preserves_remaining_displacement(self):
        self.portal();self.move(self.a,[0,0,-1,0,0,0],.6)
        info=self.a.info();self.assertEqual(info["room"],"room-b");self.assertVec(info["pose"]["p"],[4,0,1.9])
    @case("P07","room.portal_aperture")
    def test_movement_outside_portal_rectangle_does_not_switch_rooms(self):
        self.portal();b=self.actor("off-axis",pose_=pose([3,0,0]));self.move(b,[0,0,-1,0,0,0],.6)
        self.assertEqual(b.info()["room"],"room-a")
    @case("P07","room.portal_backface")
    def test_back_to_front_crossing_does_not_activate_one_way_portal(self):
        self.portal();b=self.actor("behind",pose_=pose([0,0,-1]));self.move(b,[0,0,1,0,0,0],.6)
        self.assertEqual(b.info()["room"],"room-a")
    @case("P07","room.portal_roundtrip")
    def test_walk_to_other_room_turn_and_walk_back(self):
        self.portal();q=axis_angle([0,1,0],math.pi)
        self.obj("return",room="room-b",kind="portal",pose=pose([4,0,2],q),size=[2,3],
                 destination={"room":"room-a","pose":pose([0,0,-.5],q)})
        self.move(self.a,[0,0,-1,0,0,0],.6)
        self.move(self.a,[0,0,0,0,1,0],math.pi)
        self.move(self.a,[0,0,-1,0,0,0],.6)
        self.assertEqual(self.a.info()["room"],"room-a");self.assertVec(self.a.info()["pose"]["p"],[0,0,0],2e-5)
    @case("P07","room.portal_references")
    def test_referenced_destination_cannot_be_deleted(self):
        self.portal();r=self.get("room-b")
        self.error("IN_USE","world.delete",id=r["id"],expected_revision=r["revision"])
    @case("P07","collision.swept_sphere")
    def test_thin_wall_cannot_be_tunnelled_through_in_one_tick(self):
        bounds={"min":[.3,-3,-3],"max":[.31,3,3]}
        # Both endpoints are outside the expanded wall: endpoint-only collision
        # would miss this. Call the same production sweep used by actor motion.
        hit=self.call("geometry.sweep_sphere_aabb",origin=[0,0,0],displacement=[1,0,0],radius=.2,bounds=bounds)["hit"]
        self.assertIsNotNone(hit);self.assertAlmostEqual(hit["fraction"],.1,delta=1e-6)
        self.assertVec(hit["position"],[.1,0,0]);self.assertVec(hit["normal"],[-1,0,0])
        self.obj("wall",kind="barrier",bounds=bounds)
        self.a.act("input",axes=[1,0,0,0,0,0]);self.advance(.25)
        self.assertAlmostEqual(self.a.info()["pose"]["p"][0],.1,delta=1e-5) # actor radius .2
    @case("P07","collision.slide")
    def test_motion_slides_along_wall(self):
        self.obj("wall",kind="barrier",bounds={"min":[.3,-3,-3],"max":[.31,3,3]})
        self.a.act("input",axes=[1,1,0,0,0,0]);self.advance(.2)
        self.assertVec(self.a.info()["pose"]["p"],[.1,.2,0],1e-5)
    @case("P07","object.grab_commit")
    def test_grab_move_commit_persists_pose(self):
        self.obj(pose=pose([0,0,-1]));self.a.act("object.grab",id="object-a")
        self.a.act("object.move",pose=pose([1,2,-3]));self.a.act("object.release",commit=True)
        self.assertPose(self.get("object-a")["pose"],pose([1,2,-3]));self.restart()
        self.assertPose(self.get("object-a")["pose"],pose([1,2,-3]))
    @case("P07","object.grab_cancel")
    def test_cancel_restores_original_pose(self):
        self.obj(pose=pose([0,0,-1]));before=self.get("object-a")["pose"]
        self.a.act("object.grab",id="object-a");self.a.act("object.move",pose=pose([1,2,-3]));self.a.act("object.release",commit=False)
        self.assertPose(self.get("object-a")["pose"],before)
    @case("P07","object.grab_mutual_exclusion")
    def test_exactly_one_of_two_agents_can_grab(self):
        self.obj(pose=pose([0,0,-1]));b=self.actor("b")
        def attempt(actor):
            try:actor.act("object.grab",id="object-a");return "ok"
            except RpcError as e:return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[self.a,b]))
        self.assertCountEqual(results,["ok","BUSY"])
    @case("P07","object.grab_disconnect")
    def test_disconnect_rolls_back_uncommitted_drag_and_releases_lease(self):
        self.obj(pose=pose([0,0,-1]));self.a.act("object.grab",id="object-a")
        self.a.act("object.move",pose=pose([8,8,8]));self.call("actor.close",token=self.a.token)
        self.assertPose(self.get("object-a")["pose"],pose([0,0,-1]))
        b=self.actor("b");b.act("object.grab",id="object-a");b.act("object.release",commit=True)
    @case("P07","object.reach")
    def test_out_of_reach_objects_cannot_be_grabbed(self):
        self.obj(pose=pose([0,0,-100]));self.actor_error("OUT_OF_REACH",self.a,"object.grab",id="object-a")
    @case("P07","object.cross_room_denial")
    def test_object_in_other_room_cannot_be_grabbed(self):
        self.room("room-b");self.obj(room="room-b",pose=pose([0,0,-1]))
        self.actor_error("NOT_VISIBLE",self.a,"object.grab",id="object-a")
    @case("P07","object.permission")
    def test_navigation_only_actor_cannot_change_world(self):
        self.obj(pose=pose([0,0,-1]));b=self.actor("readonly",grants=["navigate"])
        self.actor_error("FORBIDDEN",b,"object.grab",id="object-a")
    @case("P07","actor.observation")
    def test_observation_contains_own_pose_and_only_visible_room_entities(self):
        self.obj("front",kind="panel",pose=pose([0,0,-2]),size=[1,1],color=[255,255,255])
        self.room("room-b");self.obj("other",room="room-b",kind="panel",pose=pose([0,0,-2]),size=[1,1],color=[255,255,255])
        obs=self.a.act("observe");ids=[v["id"] for v in obs["visible"]]
        self.assertIn("front",ids);self.assertNotIn("other",ids);self.assertEqual(obs["actor"],self.a.id)
        self.assertPose(obs["pose"],self.a.info()["pose"])
