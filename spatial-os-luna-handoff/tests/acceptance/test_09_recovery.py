import os
import signal
from pathlib import Path
from harness.base import Acceptance,case
from harness.git_fixture import GitRepo
from harness.oracles import pose
from harness.rpc import TransportError
from harness.xbase import XAcceptance

class RecoveryTests(Acceptance):
    def setUp(self):
        super().setUp();self.room();self.obj(label="before")
    def crash_at(self,point,method,**params):
        self.call("fault.arm",point=point,action="exit")
        with self.assertRaises(TransportError):self.call(method,**params)
        # A JSON claim of a crash is not sufficient. The adapter must actually exit
        # when its production worker takes the failpoint.
        self.client.proc.wait(timeout=5)
        self.restart(crash=True)
    @case("P09","recovery.before_commit")
    def test_crash_before_commit_keeps_old_state(self):
        before=self.get("object-a")
        self.crash_at("world.before_commit","world.update",id="object-a",expected_revision=before["revision"],patch={"label":"after"})
        self.assertEqual(self.get("object-a"),before)
    @case("P09","recovery.after_commit_before_reply")
    def test_commit_survives_lost_reply(self):
        before=self.get("object-a")
        self.crash_at("world.after_commit_before_reply","world.update",id="object-a",expected_revision=before["revision"],patch={"label":"after"})
        after=self.get("object-a");self.assertEqual(after["label"],"after");self.assertEqual(after["revision"],before["revision"]+1)
        self.error("REVISION_CONFLICT","world.update",id="object-a",expected_revision=before["revision"],patch={"label":"again"})
    @case("P09","recovery.storage_failure")
    def test_injected_write_failure_does_not_ack_or_partially_update(self):
        before=self.get("object-a")
        self.call("fault.arm",point="storage.next_write",action="error",errno="ENOSPC")
        self.error("STORAGE_ERROR","world.update",id="object-a",expected_revision=before["revision"],patch={"label":"not-durable"})
        self.assertEqual(self.get("object-a"),before)
        self.restart(crash=True);self.assertEqual(self.get("object-a"),before)
    @case("P09","recovery.git_side_effect_boundary")
    def test_git_effect_completed_before_db_ack_is_reconciled_once(self):
        repo=GitRepo(self.run_dir);backing=self.call("git.repo.import",id="repo",path=str(repo.path))
        args={"repo":"repo","id":"branch-room","branch":"agent/recovered","start":"main","operation_id":"recover-create"}
        self.call("fault.arm",point="git.after_worktree_before_ready",action="exit")
        with self.assertRaises(TransportError):self.call("git.worktree.create",**args)
        self.client.proc.wait(timeout=5)
        # Observe the real external side effect BEFORE restarting/reconciling.
        self.assertEqual(repo.run("worktree","list","--porcelain",cwd=backing["path"]).count("branch refs/heads/agent/recovered"),1)
        self.restart(crash=True)
        result=self.call("git.worktree.create",**args)
        self.assertEqual(repo.branch(result["path"]),"agent/recovered")
        self.assertEqual(repo.run("worktree","list","--porcelain",cwd=backing["path"]).count("branch refs/heads/agent/recovered"),1)
        self.assertEqual(self.call("git.operation",operation_id="recover-create")["state"],"ready")
    @case("P09","recovery.kill_repeatedly")
    def test_repeated_sigkills_preserve_last_acknowledged_revision(self):
        for n in range(8):
            updated=self.patch("object-a",{"label":f"revision-{n}"})
            self.restart(crash=True);self.assertEqual(self.get("object-a"),updated)
    @case("P09","recovery.uncommitted_drag")
    def test_crash_does_not_commit_preview_transform(self):
        self.patch("object-a",{"pose":pose([0,0,-1])});a=self.actor()
        a.act("object.grab",id="object-a");a.act("object.move",pose=pose([5,6,7]));self.restart(crash=True)
        self.assertPose(self.get("object-a")["pose"],pose([0,0,-1]))
    @case("P09","recovery.process_identity")
    def test_worker_identities_refer_to_real_owned_processes(self):
        identities=self.call("runtime.identities")
        pids=[identities["worldd_pid"],identities["spatialwm_pid"]]
        self.assertEqual(len(set(pids)),2)
        for pid in pids:
            self.assertGreater(pid,1);self.assertNotEqual(pid,os.getpid())
            self.assertTrue(Path(f"/proc/{pid}").is_dir())
            self.assertEqual(os.getpgid(pid),self.client.proc.pid,"Worker must belong to this test runtime, not user's session")
    @case("P09","recovery.real_worker_death")
    def test_worker_death_invalidates_session_then_restart_recovers(self):
        identity=self.call("runtime.identities");pid=identity["worldd_pid"]
        self.assertGreater(pid,1);self.assertEqual(os.getpgid(pid),self.client.proc.pid)
        before=self.get("object-a")
        os.kill(pid,signal.SIGKILL)
        self.client.proc.wait(timeout=5)
        self.restart(crash=True);self.assertEqual(self.get("object-a"),before)

class SlotRecoveryTests(XAcceptance):
    @case("P09","surface.durable_slot")
    def test_replacement_x_window_attaches_to_durable_slot_not_old_xid(self):
        slot=self.obj("editor-slot",kind="surface_slot",pose=pose([2,1,-5]),size=[3,2])
        p,s=self.probe();self.call("surface.attach_slot",id=s["id"],slot=slot["id"])
        self.assertPose(self.surface(s["id"])["pose"],slot["pose"])
        p.command("destroy");self.fence();self.error("NOT_FOUND","surface.get",id=s["id"])
        self.assertPose(self.get(slot["id"])["pose"],slot["pose"])
        other,new=self.probe();self.assertNotEqual(new["id"],s["id"])
        self.call("surface.attach_slot",id=new["id"],slot=slot["id"])
        self.assertPose(self.surface(new["id"])["pose"],slot["pose"])
        self.assertEqual(self.surface(new["id"])["slot"],slot["id"])
    @case("P09","surface.stale_handle")
    def test_old_surface_handle_cannot_control_new_window(self):
        p,s=self.probe();old={"id":s["id"],"generation":s["generation"]};self.aim()
        p.command("destroy");self.fence();q,new=self.probe()
        self.error("STALE_TARGET","surface.validate",handle=old)
        self.assertEqual([e for e in q.input_events() if e["event"] in ("button","key")],[])
