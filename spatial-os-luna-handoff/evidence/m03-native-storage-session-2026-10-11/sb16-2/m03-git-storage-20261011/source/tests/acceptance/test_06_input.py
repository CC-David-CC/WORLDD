from concurrent.futures import ThreadPoolExecutor
from harness.base import case
from harness.xbase import XAcceptance
from harness.oracles import pose

class InputTests(XAcceptance):
    xi2=True
    @case("P06","input.ray_to_client_pixel")
    def test_click_reaches_real_client_at_expected_pixel(self):
        p,s=self.probe();self.aim(direction=(-.5,.5,-4));mark=len(p.events);self.click()
        presses=[e for e in p.input_events(mark) if e["event"]=="button" and e["pressed"]]
        self.assertEqual(len(presses),1);self.assertVec([presses[0]["x"],presses[0]["y"]],[80,60],1)
        self.assertEqual(presses[0]["xid"],p.xid);self.assertGreater(presses[0]["device"],0)
    @case("P06","input.right_middle_buttons")
    def test_each_mouse_button_preserves_identity(self):
        p,s=self.probe();self.aim();mark=len(p.events)
        for b in (1,2,3):
            self.a.act("pointer",button=b,pressed=True);self.a.act("pointer",button=b,pressed=False)
        self.fence();events=[(e["button"],e["pressed"]) for e in p.input_events(mark) if e["event"]=="button"]
        self.assertEqual(events,[(1,True),(1,False),(2,True),(2,False),(3,True),(3,False)])
    @case("P06","input.double_click")
    def test_double_click_is_two_real_balanced_clicks(self):
        p,s=self.probe();self.aim();mark=len(p.events)
        self.click();self.advance(.05);self.click()
        events=[e["pressed"] for e in p.input_events(mark) if e["event"]=="button" and e["button"]==1]
        self.assertEqual(events,[True,False,True,False])
    @case("P06","input.focus_keyboard")
    def test_keyboard_is_received_by_focused_client(self):
        p,s=self.probe();self.aim();self.click();mark=len(p.events)
        self.a.act("key",keysym="a",pressed=True);self.a.act("key",keysym="a",pressed=False);self.fence()
        events=[e for e in p.input_events(mark) if e["event"]=="key"]
        self.assertEqual([e["pressed"] for e in events],[True,False])
        self.assertEqual(events[0]["keysym"],ord("a"));self.assertEqual(events[0]["text_hex"],"61")
    @case("P06","input.modifiers")
    def test_modifier_state_affects_real_client_text(self):
        p,s=self.probe();self.aim();self.click();mark=len(p.events)
        for k,down in [("Shift_L",True),("a",True),("a",False),("Shift_L",False)]:self.a.act("key",keysym=k,pressed=down)
        self.fence();letters=[e for e in p.input_events(mark) if e["event"]=="key" and e["pressed"] and e["text_hex"]]
        self.assertEqual([e["text_hex"] for e in letters],["41"])
        self.assertEqual(self.a.info()["held_keys"],[])
    @case("P06","input.no_focus")
    def test_typing_without_focus_is_rejected(self):
        p,s=self.probe();mark=len(p.events)
        self.actor_error("NO_FOCUS",self.a,"key",keysym="a",pressed=True);self.fence()
        self.assertEqual([e for e in p.input_events(mark) if e["event"]=="key"],[])
    @case("P06","input.occlusion")
    def test_hidden_client_does_not_receive_click(self):
        far,fs=self.probe(p=(0,0,-5));near,ns=self.probe(p=(0,0,-3));self.aim()
        fm,nm=len(far.events),len(near.events);self.click()
        self.assertEqual([e for e in far.input_events(fm) if e["event"]=="button"],[])
        self.assertEqual(len([e for e in near.input_events(nm) if e["event"]=="button" and e["pressed"]]),1)
    @case("P06","input.background_no_clickthrough")
    def test_miss_does_not_fall_through_to_x_root_geometry(self):
        p,s=self.probe();self.aim(direction=(1,0,0));mark=len(p.events)
        self.actor_error("NO_TARGET",self.a,"pointer",button=1,pressed=True);self.fence()
        self.assertEqual([e for e in p.input_events(mark) if e["event"]=="button"],[])
    @case("P06","input.stale_surface_generation")
    def test_resize_invalidates_previously_aimed_target(self):
        p,s=self.probe();self.aim();p.command("resize",640,480)
        self.until(lambda:self.surface(s["id"]),lambda x:x["pixels"]==[640,480]);mark=len(p.events)
        self.actor_error("STALE_TARGET",self.a,"pointer",button=1,pressed=True);self.fence()
        self.assertEqual([e for e in p.input_events(mark) if e["event"]=="button"],[])
        self.aim();self.click()
        e=p.wait(lambda e:e["event"]=="button" and e["pressed"],after=mark)
        self.assertVec([e["x"],e["y"]],[320,240],1)
    @case("P06","input.drag_capture")
    def test_drag_release_goes_to_original_target(self):
        left,ls=self.probe(p=(-2,0,-4));right,rs=self.probe(p=(2,0,-4))
        self.aim(direction=(-2,0,-4));lm,rm=len(left.events),len(right.events)
        self.a.act("pointer",button=1,pressed=True)
        self.aim(direction=(2,0,-4));self.a.act("pointer",button=1,pressed=False);self.fence()
        self.assertEqual([e["pressed"] for e in left.input_events(lm) if e["event"]=="button"],[True,False])
        self.assertEqual([e for e in right.input_events(rm) if e["event"]=="button"],[])
    @case("P06","input.cancellation_releases")
    def test_stop_releases_keys_and_buttons_in_real_client(self):
        p,s=self.probe();self.aim();self.click();mark=len(p.events)
        self.a.act("key",keysym="Control_L",pressed=True);self.a.act("pointer",button=1,pressed=True)
        self.a.act("stop");self.fence();events=p.input_events(mark)
        self.assertEqual([e["pressed"] for e in events if e["event"]=="key"],[True,False])
        self.assertEqual([e["pressed"] for e in events if e["event"]=="button"],[True,False])
        self.assertEqual(self.a.info()["held_keys"],[]);self.assertEqual(self.a.info()["held_buttons"],[])
    @case("P06","input.disconnect_releases")
    def test_disconnect_releases_in_native_client(self):
        p,s=self.probe();self.aim();self.click();mark=len(p.events)
        self.a.act("key",keysym="Shift_L",pressed=True);self.call("actor.close",token=self.a.token);self.fence()
        self.assertEqual([e["pressed"] for e in p.input_events(mark) if e["event"]=="key"],[True,False])
    @case("P06","input.scroll")
    def test_vertical_scroll_generates_balanced_client_scroll_events(self):
        p,s=self.probe();self.aim();mark=len(p.events)
        self.a.act("scroll",steps_y=1);self.a.act("scroll",steps_y=-1);self.fence()
        self.assertEqual([(e["button"],e["pressed"]) for e in p.input_events(mark) if e["event"]=="button"],[(4,True),(4,False),(5,True),(5,False)])
    @case("P06","input.permission")
    def test_actor_without_interaction_grant_cannot_type(self):
        p,s=self.probe();b=self.actor("denied",grants=["navigate"]);mark=len(p.events)
        self.actor_error("FORBIDDEN",b,"key",keysym="a",pressed=True);self.fence()
        self.assertEqual([e for e in p.input_events(mark) if e["event"]=="key"],[])
    @case("P06","input.no_hidden_atlas")
    def test_interaction_does_not_move_native_window_to_private_slot(self):
        p,s=self.probe();before=p.geometry();self.aim();self.click();after=p.geometry()
        self.assertEqual((before["x"],before["y"]),(0,0),"Witness requested root position (0,0); do not pre-park it in an atlas")
        self.assertEqual((before["x"],before["y"]),(after["x"],after["y"]))
    @case("P06","input.independent_seats")
    def test_two_agents_type_concurrently_without_stealing_focus(self):
        p1,s1=self.probe(p=(-2,0,-4));p2,s2=self.probe(p=(2,0,-4))
        g1,g2=p1.geometry(),p2.geometry()
        self.assertEqual((g1["x"],g1["y"]),(0,0));self.assertEqual((g2["x"],g2["y"]),(0,0))
        a=self.actor("left",pose_=pose([-2,0,0]));b=self.actor("right",pose_=pose([2,0,0]))
        self.aim(a);self.click(a);self.aim(b);self.click(b);m1,m2=len(p1.events),len(p2.events)
        def type_letters(actor,key):
            for _ in range(25):
                actor.act("key",keysym=key,pressed=True);actor.act("key",keysym=key,pressed=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1=pool.submit(type_letters,a,"a");f2=pool.submit(type_letters,b,"b");f1.result();f2.result()
        self.fence()
        e1=[e for e in p1.input_events(m1) if e["event"]=="key" and e["pressed"]]
        e2=[e for e in p2.input_events(m2) if e["event"]=="key" and e["pressed"]]
        self.assertEqual([e["text_hex"] for e in e1],["61"]*25);self.assertEqual([e["text_hex"] for e in e2],["62"]*25)
        self.assertEqual(len({e["device"] for e in e1}),1);self.assertEqual(len({e["device"] for e in e2}),1)
        self.assertNotEqual(e1[0]["device"],e2[0]["device"])
    @case("P06","input.destroy_during_drag")
    def test_destroy_during_drag_clears_held_state_without_retargeting(self):
        p,s=self.probe();self.aim();self.a.act("pointer",button=1,pressed=True)
        p.command("destroy");self.fence()
        self.assertEqual(self.a.info()["held_buttons"],[]);self.assertIsNone(self.a.info()["focus"])
    @case("P06","input.one_surface_lease")
    def test_two_actors_cannot_mutate_one_surface_at_once(self):
        p,s=self.probe();b=self.actor("other");self.aim();self.click();self.aim(b)
        self.actor_error("BUSY",b,"pointer",button=1,pressed=True)
        self.a.act("unfocus");self.aim(b);self.click(b)
        self.assertEqual(b.info()["focus"]["id"],s["id"])
