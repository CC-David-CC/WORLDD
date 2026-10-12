"""End-to-end stories use the same production actor primitives as individual tests."""
import math
import sys
from pathlib import Path
from harness.base import case
from harness.git_fixture import GitRepo
from harness.oracles import pose,axis_angle
from harness.xbase import XAcceptance

class WalkthroughTests(XAcceptance):
    xi2=True
    def walk(self,axes,seconds):
        while seconds>1e-10:
            dt=min(.2,seconds);self.a.act("input",axes=axes);self.advance(dt);seconds-=dt
        self.a.act("stop")
    @case("P10","story.walk_work_return")
    def test_walk_to_room_use_real_window_work_in_selected_workspace_and_return(self):
        self.room("lab")
        self.obj("door",kind="portal",pose=pose([0,0,-.5]),size=[2,3],destination={"room":"lab","pose":pose([0,0,0])})
        q=axis_angle([0,1,0],math.pi)
        self.obj("back",kind="portal",room="lab",pose=pose([0,0,0],q),size=[2,3],destination={"room":"room-a","pose":pose([0,0,-.5],q)})
        native,surf=self.probe(room="lab")
        repo=GitRepo(self.run_dir);self.call("git.repo.import",id="repo",path=str(repo.path))
        w=self.a.act("git.worktree.create",repo="repo",id="experiment",branch="agent/experiment",start="main",operation_id="story-create")
        rights=["workspace.select","files.read","files.write","process.launch"]
        self.call("workspace.create",id="experiment-workspace",worktree=w["id"],policy=rights)
        self.call("workspace.member.set",id="experiment-workspace",actor=self.a.id,rights=rights)
        self.call("workspace.available",id="experiment-workspace",available=True)
        self.assertIsNone(self.a.info()["workspace"])
        self.a.act("workspace.select",id="experiment-workspace")
        before,_=self.capture(self.a);self.assertColor(before.mean_patch(64,64),[10,12,16])
        self.walk([0,0,-1,0,0,0],.6);self.assertEqual(self.a.info()["room"],"lab")
        self.assertEqual(self.a.info()["workspace"],"experiment-workspace")
        self.aim();self.click();mark=len(native.events)
        self.a.act("key",keysym="x",pressed=True);self.a.act("key",keysym="x",pressed=False);self.fence()
        self.assertEqual([e["text_hex"] for e in native.input_events(mark) if e["event"]=="key" and e["pressed"]],["78"])
        launched=self.a.act("process.launch",argv=[sys.executable,"-c","from pathlib import Path;Path('agent-result.txt').write_text('done\\n')"],operation_id="story-process",profile="development")
        self.assertEqual(self.call("process.wait",id=launched["process_id"],timeout_seconds=5)["exit_code"],0)
        result=Path(w["path"])/"agent-result.txt"
        self.assertEqual(result.read_text(),"done\n");self.assertFalse((repo.path/"agent-result.txt").exists())
        self.walk([0,0,0,0,1,0],math.pi);self.walk([0,0,-1,0,0,0],.6)
        self.assertEqual(self.a.info()["room"],"room-a");self.assertVec(self.a.info()["pose"]["p"],[0,0,0],2e-5)
        self.assertEqual(result.read_text(),"done\n");self.assertIsNone(self.a.info()["focus"])
        self.assertEqual(self.a.info()["workspace"],"experiment-workspace")
        self.assertEqual(repo.branch(w["path"]),"agent/experiment")
    @case("P10","story.observe_act_verify")
    def test_agent_observation_and_received_input_agree_on_target(self):
        native,s=self.probe();agent=self.actor("worker")
        observation=agent.act("observe")
        self.assertIn(s["id"],[e["id"] for e in observation["visible"]])
        hit=self.aim(agent);self.assertEqual(hit["id"],s["id"])
        mark=len(native.events);self.click(agent)
        event=native.wait(lambda e:e["event"]=="button" and e["pressed"],after=mark)
        self.assertVec([event["x"],event["y"]],hit["pixel"],1)
        self.assertEqual(agent.info()["focus"]["id"],s["id"])
        agent.act("unfocus");self.assertIsNone(agent.info()["focus"])
    @case("P10","story.cancel_without_harming_other_actor")
    def test_cancelled_agent_leaves_human_usable(self):
        native,s=self.probe();agent=self.actor("worker");self.aim(agent);self.click(agent)
        agent.act("key",keysym="Control_L",pressed=True);self.call("actor.close",token=agent.token)
        self.fence();self.aim();self.click();mark=len(native.events)
        self.a.act("key",keysym="a",pressed=True);self.a.act("key",keysym="a",pressed=False);self.fence()
        typed=[e for e in native.input_events(mark) if e["event"]=="key" and e["pressed"]]
        self.assertEqual([e["text_hex"] for e in typed],["61"])
