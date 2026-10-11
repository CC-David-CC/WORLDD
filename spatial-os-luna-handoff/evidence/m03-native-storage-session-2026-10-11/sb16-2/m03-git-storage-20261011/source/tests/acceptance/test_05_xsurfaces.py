import secrets
from harness.base import case
from harness.xbase import XAcceptance
from harness.oracles import PPM

class XSurfaceTests(XAcceptance):
    @case("P05","x11.real_surface_import")
    def test_independent_x_window_becomes_surface(self):
        p,s=self.probe();self.assertEqual(s["pixels"],[320,240]);self.assertTrue(s["mapped"])
        self.assertGreaterEqual(s["generation"],1)
    @case("P05","x11.texture_orientation")
    def test_all_four_quadrants_reach_correct_frame_locations(self):
        self.probe();self.fence();image,_=self.capture(self.a)
        for xy,color in [((56,56),(255,0,0)),((72,56),(0,255,0)),((56,72),(0,0,255)),((72,72),(255,255,255))]:
            self.assertColor(image.mean_patch(*xy),color)
    @case("P05","x11.damage_updates")
    def test_random_client_paint_appears_in_compositor(self):
        p,s=self.probe()
        for _ in range(5):
            color=[secrets.randbelow(200)+25 for _ in range(3)]
            p.command("paint",*color);self.fence();image,_=self.capture(self.a)
            self.assertColor(image.mean_patch(64,64),color)
    @case("P05","x11.resize_reacquire")
    def test_resize_changes_pixmap_generation_not_world_identity(self):
        p,s=self.probe();before=self.surface(s["id"])
        p.command("resize",641,479)
        after=self.until(lambda:self.surface(s["id"]),lambda x:x["pixels"]==[641,479])
        self.assertEqual(after["id"],before["id"]);self.assertGreater(after["generation"],before["generation"])
        self.assertPose(after["pose"],before["pose"]);self.assertEqual(after["size"],before["size"])
        p.command("paint",11,77,199);self.fence();image,_=self.capture(self.a)
        self.assertColor(image.mean_patch(64,64),[11,77,199])
    @case("P05","x11.unmap_remap")
    def test_unmapped_not_visible_or_interactive_then_returns(self):
        p,s=self.probe();oldgen=s["generation"];p.command("unmap")
        self.until(lambda:self.surface(s["id"]),lambda x:not x["mapped"])
        self.fence();image,_=self.capture(self.a);self.assertColor(image.mean_patch(64,64),[10,12,16])
        self.assertIsNone(self.aim())
        p.command("map");after=self.until(lambda:self.surface(s["id"]),lambda x:x["mapped"])
        self.assertGreater(after["generation"],oldgen)
    @case("P05","x11.destroy")
    def test_destroy_drops_resource_and_pick(self):
        p,s=self.probe();p.command("destroy");self.fence()
        self.error("NOT_FOUND","surface.get",id=s["id"]);self.assertIsNone(self.aim())
    @case("P05","x11.popup_relationship")
    def test_transient_is_attached_not_orphaned(self):
        p,s=self.probe();p.command("popup",80,60)
        native=p.wait(lambda e:e["event"]=="popup")
        children=self.until(lambda:self.call("surface.children",id=s["id"])["surfaces"],lambda x:len(x)==1)
        self.assertEqual(children[0]["xid"],native["xid"])
        self.assertEqual(children[0]["parent_id"],s["id"]);self.assertEqual(children[0]["room"],s["room"])
    @case("P05","x11.popup_parent_move")
    def test_child_follows_parent_room_and_transform(self):
        p,s=self.probe();p.command("popup",80,60);self.room("room-b");self.fence()
        before=self.until(lambda:self.call("surface.children",id=s["id"])["surfaces"],lambda items:len(items)==1)[0]
        from harness.oracles import pose
        destination=[4,2,-6]
        delta=[destination[i]-s["pose"]["p"][i] for i in range(3)]
        self.call("surface.place",id=s["id"],room="room-b",pose=pose(destination),size=s["size"])
        self.fence();children=self.call("surface.children",id=s["id"])["surfaces"]
        self.assertEqual(len(children),1);self.assertEqual(children[0]["room"],"room-b")
        self.assertEqual(children[0]["parent_id"],s["id"])
        self.assertPose(children[0]["pose"],pose([before["pose"]["p"][i]+delta[i] for i in range(3)],before["pose"]["q"]))
    @case("P05","x11.output_independent_readback")
    def test_native_readback_sees_the_compositor_output(self):
        p,s=self.probe();color=[secrets.randbelow(180)+30 for _ in range(3)]
        p.command("paint",*color);self.fence()
        output=self.call("render.present",token=self.a.token,width=128,height=128)
        path=self.run_dir/"independent-output.ppm";p.capture(output["xid"],path)
        im=PPM(path);self.assertEqual((im.width,im.height),(128,128));self.assertColor(im.mean_patch(64,64),color)
    @case("P05","x11.resource_lifecycle")
    def test_resize_storm_has_no_outstanding_retired_resources(self):
        p,s=self.probe()
        for i in range(40):p.command("resize",320+i%5,240+i%7)
        self.fence();stats=self.call("runtime.resources")
        self.assertEqual(stats["retired_pixmaps_pending"],0);self.assertEqual(stats["invalid_texture_bindings"],0)
        p.command("paint",91,117,233);self.fence();im,_=self.capture(self.a);self.assertColor(im.mean_patch(64,64),[91,117,233])
