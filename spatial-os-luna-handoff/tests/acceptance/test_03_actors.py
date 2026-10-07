import math
from harness.base import Acceptance,case
from harness.oracles import pose,axis_angle
from harness.rpc import RpcError
from harness.wire import make_request

class ActorTests(Acceptance):
    def setUp(self):
        super().setUp(); self.room()
    @case("P03","actor.identity")
    def test_human_and_agent_have_independent_state(self):
        a=self.actor("human"); b=self.actor("agent")
        a.act("input",axes=[1,0,0,0,0,0]); self.advance(.1)
        self.assertVec(a.info()["pose"]["p"],[.1,0,0]); self.assertVec(b.info()["pose"]["p"],[0,0,0])
    @case("P03","actor.local_frame")
    def test_forward_uses_camera_orientation(self):
        a=self.actor(pose_=pose(q=axis_angle([0,1,0],math.pi/2)))
        a.act("input",axes=[0,0,-1,0,0,0]); self.advance(.1)
        self.assertVec(a.info()["pose"]["p"],[-.1,0,0])
    @case("P03","actor.deadzone")
    def test_deadzone_is_rescaled_not_subtracted_every_tick(self):
        a=self.actor(); a.act("input",axes=[.02,0,0,0,0,0]); self.advance(.1)
        self.assertVec(a.info()["pose"]["p"],[0,0,0])
        a.act("input",axes=[.525,0,0,0,0,0]); self.advance(.1)
        self.assertVec(a.info()["pose"]["p"],[.05,0,0])
    @case("P03","actor.input_validation")
    def test_axis_count_and_range_checked(self):
        a=self.actor()
        for axes in ([0]*5,[0]*7,[2,0,0,0,0,0]):
            self.actor_error("INVALID_ARGUMENT",a,"input",axes=axes)
        # Protobuf double axes cannot hold a JSON string. Send a wrong wire type.
        a.seq += 1
        request=make_request("actor.act",{"token":a.token,"seq":a.seq,"op":"input","args":{"axes":[0]*6}})
        request.actor_act.input.MergeFromString(b'\x0dxxxx')
        with self.assertRaises(RpcError) as rejected:
            self.client.call_message(request)
        self.assertEqual(rejected.exception.code,"INVALID_ARGUMENT")
    @case("P03","actor.stale_sequence")
    def test_replayed_sequence_not_applied_twice(self):
        a=self.actor(); a.act("input",axes=[1,0,0,0,0,0])
        self.error("STALE_SEQUENCE","actor.act",token=a.token,seq=1,op="input",args={"axes":[-1,0,0,0,0,0]})
        self.advance(.1); self.assertVec(a.info()["pose"]["p"],[.1,0,0])
    @case("P03","actor.deadman")
    def test_stale_input_stops_at_exact_timeout_boundary(self):
        a=self.actor(); a.act("input",axes=[1,0,0,0,0,0]); self.advance(1)
        self.assertVec(a.info()["pose"]["p"],[.25,0,0])
        self.assertVec(a.info()["axes"],[0]*6)
    @case("P03","actor.deadman_refresh")
    def test_new_sample_refreshes_deadman_not_unrelated_commands(self):
        a=self.actor(); a.act("input",axes=[1,0,0,0,0,0]); self.advance(.2)
        a.info(); self.advance(.1)
        self.assertVec(a.info()["pose"]["p"],[.25,0,0])
        a.act("input",axes=[1,0,0,0,0,0]); self.advance(.1)
        self.assertVec(a.info()["pose"]["p"],[.35,0,0])
    @case("P03","actor.stop")
    def test_stop_is_immediate_and_idempotent(self):
        a=self.actor(); a.act("input",axes=[1,0,0,0,0,0]); self.advance(.1)
        a.act("stop"); a.act("stop"); self.advance(1)
        self.assertVec(a.info()["pose"]["p"],[.1,0,0]); self.assertEqual(a.info()["held_keys"],[])
    @case("P03","actor.disconnect")
    def test_disconnect_revokes_token_and_stops_motion(self):
        a=self.actor(); a.act("input",axes=[1,0,0,0,0,0]); self.advance(.1)
        self.call("actor.close",token=a.token); self.advance(1)
        self.error("UNAUTHORIZED","actor.inspect",token=a.token)
        self.assertVec(self.get(a.id)["pose"]["p"],[.1,0,0])
    @case("P03","actor.permissions")
    def test_missing_navigation_grant_blocks_motion(self):
        a=self.actor(grants=["interact"])
        self.actor_error("FORBIDDEN",a,"input",axes=[1,0,0,0,0,0])
        self.advance(.1); self.assertVec(a.info()["pose"]["p"],[0,0,0])
    @case("P03","actor.token_binding")
    def test_payload_cannot_impersonate_another_actor(self):
        a=self.actor("a"); b=self.actor("b")
        a.seq += 1
        request=make_request("actor.act",{"token":a.token,"seq":a.seq,"op":"input","args":{"axes":[1,0,0,0,0,0]}})
        # An unknown actor-selector field must not override the authenticated token.
        claimed=b.id.encode("utf-8")
        request.actor_act.input.MergeFromString(b'\xa2\x06'+bytes([len(claimed)])+claimed)
        with self.assertRaises(RpcError) as rejected:
            self.client.call_message(request)
        self.assertEqual(rejected.exception.code,"INVALID_ARGUMENT")
        self.assertVec(b.info()["pose"]["p"],[0,0,0])
    @case("P03","actor.checkpoint")
    def test_checkpoint_restores_pose_but_not_held_input_or_token(self):
        a=self.actor(); a.act("input",axes=[1,0,0,0,0,0]); self.advance(.1)
        self.call("world.checkpoint"); self.restart(crash=True)
        self.error("UNAUTHORIZED","actor.inspect",token=a.token)
        restored=self.get(a.id); self.assertVec(restored["pose"]["p"],[.1,0,0])
        b=self.actor(ident="human")
        self.advance(1); self.assertVec(b.info()["pose"]["p"],[.1,0,0]); self.assertEqual(b.info()["held_keys"],[])
    @case("P03","actor.frame_partition")
    def test_motion_matches_with_different_clock_partitions(self):
        a=self.actor("a"); b=self.actor("b")
        a.act("input",axes=[1,0,-1,0,.5,0]); self.advance(.2); p=a.info()["pose"]
        a.act("stop")
        b.act("input",axes=[1,0,-1,0,.5,0])
        for _ in range(20): self.advance(.01)
        self.assertPose(b.info()["pose"],p,2e-5)
    @case("P03","actor.simultaneous_axes")
    def test_translation_axes_not_normalized_to_a_single_axis(self):
        a=self.actor(); a.act("input",axes=[1,1,1,0,0,0]); self.advance(.1)
        self.assertVec(a.info()["pose"]["p"],[.1,.1,.1])
