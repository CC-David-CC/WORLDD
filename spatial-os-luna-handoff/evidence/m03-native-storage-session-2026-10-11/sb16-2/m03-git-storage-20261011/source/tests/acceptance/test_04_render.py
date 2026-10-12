from harness.base import Acceptance,case
from harness.oracles import pose,axis_angle
import math

class RenderTests(Acceptance):
    def setUp(self):
        super().setUp(); self.room(); self.a=self.actor()
    def panel(self,ident="panel",color=(200,20,60),p=(0,0,-4),size=(2,2)):
        return self.obj(ident,kind="panel",pose=pose(p),size=list(size),color=list(color))
    @case("P04","render.real_frame")
    def test_frame_is_readable_and_has_monotonic_identity(self):
        self.panel(); image,first=self.capture(self.a)
        self.assertColor(image.mean_patch(64,64),[200,20,60])
        _,second=self.capture(self.a)
        self.assertGreater(second["frame_id"],first["frame_id"])
    @case("P04","render.projection")
    def test_fixed_fov_projects_known_quad_bounds(self):
        self.panel()
        image,_=self.capture(self.a)
        # 90-degree vertical FOV, aspect=1: x/y extent maps to +/-16 pixels.
        for xy in [(50,50),(77,50),(50,77),(77,77)]:
            self.assertColor(image.pixel(*xy),[200,20,60])
        for xy in [(44,64),(84,64),(64,44),(64,84)]:
            self.assertColor(image.pixel(*xy),[10,12,16])
    @case("P04","render.depth")
    def test_nearer_surface_occludes_farther_independent_of_creation_order(self):
        self.panel("near",(20,220,40),(0,0,-2)); self.panel("far",(220,30,30),(0,0,-4))
        image,_=self.capture(self.a); self.assertColor(image.mean_patch(64,64),[20,220,40])
    @case("P04","render.actor_view")
    def test_independent_cameras_produce_different_frames(self):
        self.panel(); other=self.actor("other",pose_=pose([10,0,0]))
        human,_=self.capture(self.a); agent,_=self.capture(other)
        self.assertColor(human.mean_patch(64,64),[200,20,60]); self.assertColor(agent.mean_patch(64,64),[10,12,16])
    @case("P04","render.room_visibility")
    def test_other_room_is_not_visible_even_at_same_coordinates(self):
        self.room("room-b"); self.obj("hidden",room="room-b",kind="panel",pose=pose([0,0,-2]),size=[2,2],color=[255,0,0])
        image,_=self.capture(self.a); self.assertColor(image.mean_patch(64,64),[10,12,16])
    @case("P04","render.backface")
    def test_back_facing_panel_not_visible_or_selectable(self):
        self.panel(); self.patch("panel",{"pose":pose([0,0,-4],axis_angle([0,1,0],math.pi))})
        image,_=self.capture(self.a); self.assertColor(image.mean_patch(64,64),[10,12,16])
        self.assertIsNone(self.a.act("aim",direction=[0,0,-1])["hit"])
    @case("P04","render.resize_output")
    def test_output_aspect_does_not_stretch_square(self):
        self.panel(); image,_=self.capture(self.a,width=256,height=128)
        self.assertColor(image.pixel(128,64),[200,20,60]); self.assertColor(image.pixel(105,64),[10,12,16])
        self.assertColor(image.pixel(128,41),[10,12,16])
    @case("P04","render.pick_depth_agreement")
    def test_pick_returns_visible_nearest_surface(self):
        self.panel("far",(255,0,0),(0,0,-4)); self.panel("near",(0,255,0),(0,0,-2))
        hit=self.a.act("aim",direction=[0,0,-1])["hit"]
        self.assertEqual(hit["id"],"near")
        self.assertAlmostEqual(hit["distance"],2); self.assertVec(hit["uv"],[.5,.5])
    @case("P04","render.damage_of_scene")
    def test_scene_edit_appears_in_next_fenced_frame(self):
        self.panel(); self.capture(self.a)
        updated=self.patch("panel",{"color":[17,89,231]})
        image,meta=self.capture(self.a)
        self.assertColor(image.mean_patch(64,64),[17,89,231])
        self.assertGreaterEqual(meta["world_revision"],updated["world_revision"])
    @case("P04","render.removal")
    def test_deleted_surface_disappears_from_pixels_and_pick(self):
        p=self.panel(); self.capture(self.a)
        self.call("world.delete",id=p["id"],expected_revision=p["revision"])
        image,_=self.capture(self.a); self.assertColor(image.mean_patch(64,64),[10,12,16])
        self.assertIsNone(self.a.act("aim",direction=[0,0,-1])["hit"])
