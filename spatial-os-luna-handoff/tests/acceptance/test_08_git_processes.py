import json
from pathlib import Path
import sys
from harness.base import Acceptance,case
from harness.git_fixture import GitRepo

class GitTests(Acceptance):
    def setUp(self):
        super().setUp();self.room();self.a=self.actor();self.repo=GitRepo(self.run_dir)
        self.call("git.repo.import",id="repo",path=str(self.repo.path))
    def worktree(self,id="work-a",branch="agent/a",operation_id="create-a"):
        return self.call("git.worktree.create",repo="repo",id=id,branch=branch,start="main",operation_id=operation_id)
    @case("P08","git.real_worktree")
    def test_create_is_a_real_git_worktree(self):
        w=self.worktree();p=Path(w["path"])
        self.assertTrue(p.is_dir());self.assertEqual(self.repo.branch(p),"agent/a");self.assertEqual(self.repo.head(p),self.repo.head())
        listed=self.repo.run("worktree","list","--porcelain");self.assertIn(str(p),listed)
    @case("P08","git.branch_not_directory")
    def test_repository_branch_commit_and_worktree_are_distinct(self):
        w=self.worktree();self.assertEqual(w["repo"],"repo");self.assertEqual(w["branch"],"agent/a")
        self.assertEqual(w["head"],self.repo.head());self.assertNotEqual(w["id"],w["head"])
        self.assertTrue(Path(w["path"]).is_relative_to(self.state_dir))
    @case("P08","git.checkout_isolation")
    def test_two_agents_do_not_share_working_files(self):
        a=self.worktree();b=self.worktree("work-b","agent/b","create-b")
        self.repo.write("README.txt","only a\n",a["path"])
        self.assertEqual(Path(b["path"],"README.txt").read_text(),"base\n")
        self.assertEqual((self.repo.path/"README.txt").read_text(),"base\n")
    @case("P08","git.duplicate_branch")
    def test_same_branch_cannot_be_force_checked_out_twice(self):
        self.worktree()
        self.error("BRANCH_IN_USE","git.worktree.create",repo="repo",id="second",branch="agent/a",start="main",operation_id="other")
        self.assertEqual(self.repo.run("worktree","list","--porcelain").count("branch refs/heads/agent/a"),1)
    @case("P08","git.invalid_ref")
    def test_invalid_branch_name_fails_without_side_effect(self):
        before=self.repo.run("worktree","list","--porcelain")
        for branch in ("../escape","bad..name","a.lock","--orphan=x"):
            self.error("INVALID_ARGUMENT","git.worktree.create",repo="repo",id="bad",branch=branch,start="main",operation_id="bad-"+branch)
        self.assertEqual(self.repo.run("worktree","list","--porcelain"),before)
    @case("P08","git.idempotency")
    def test_retry_same_operation_does_not_duplicate_worktree(self):
        a=self.worktree();b=self.worktree();self.assertEqual(a,b)
        self.assertEqual(self.repo.run("worktree","list","--porcelain").count("branch refs/heads/agent/a"),1)
    @case("P08","git.idempotency_payload_mismatch")
    def test_reused_operation_id_with_different_payload_rejected(self):
        self.worktree()
        self.error("OPERATION_CONFLICT","git.worktree.create",repo="repo",id="other",branch="other",start="main",operation_id="create-a")
    @case("P08","git.actual_status")
    def test_untracked_and_modified_are_distinct(self):
        w=self.worktree();self.repo.write("README.txt","edit\n",w["path"]);self.repo.write("new.txt","new\n",w["path"])
        s=self.call("git.status",worktree=w["id"])
        self.assertIn("README.txt",s["modified"]);self.assertIn("new.txt",s["untracked"])
        self.assertFalse(s["clean"])
    @case("P08","git.external_changes")
    def test_direct_git_commit_updates_world_view(self):
        w=self.worktree();p=w["path"];old=w["head"]
        self.repo.write("README.txt","external\n",p);self.repo.run("add","--","README.txt",cwd=p);self.repo.run("commit","-m","outside-worldd",cwd=p)
        observed=self.call("git.status",worktree=w["id"])
        self.assertEqual(observed["head"],self.repo.head(p));self.assertNotEqual(observed["head"],old)
    @case("P08","git.dirty_removal")
    def test_dirty_worktree_is_never_silently_discarded(self):
        w=self.worktree();self.repo.write("untracked.txt","valuable\n",w["path"])
        self.error("DIRTY_WORKTREE","git.worktree.remove",id=w["id"],operation_id="remove-a")
        self.assertEqual(Path(w["path"],"untracked.txt").read_text(),"valuable\n")
    @case("P08","git.clean_removal")
    def test_clean_removal_leaves_branch_intact(self):
        w=self.worktree();self.call("git.worktree.remove",id=w["id"],operation_id="remove-a")
        self.assertFalse(Path(w["path"]).exists());self.assertEqual(self.repo.run("rev-parse","agent/a"),self.repo.head())
    @case("P08","git.permission")
    def test_actor_without_git_grant_cannot_create_worktree(self):
        b=self.actor("readonly",grants=["navigate"])
        self.actor_error("FORBIDDEN",b,"git.worktree.create",repo="repo",id="denied",branch="denied",start="main",operation_id="denied")
        self.assertEqual(self.repo.run("worktree","list","--porcelain").count("worktree "),1)
    @case("P08","process.room_worktree_cwd")
    def test_launched_process_runs_in_rooms_actual_worktree(self):
        w=self.worktree();self.patch("room-a",{"worktree":w["id"]})
        output=self.run_dir/"cwd-result.json"
        script=self.run_dir/"witness.py"
        script.write_text("import json,os,sys,pathlib\npathlib.Path(sys.argv[1]).write_text(json.dumps({'cwd':os.getcwd(),'arg':sys.argv[2]}))\n")
        result=self.a.act("process.launch",argv=[sys.executable,str(script),str(output),"$(touch not-a-command)"],operation_id="launch-cwd")
        self.assertEqual(self.call("process.wait",id=result["process_id"],timeout_seconds=5)["exit_code"],0)
        data=json.loads(output.read_text());self.assertEqual(Path(data["cwd"]).resolve(),Path(w["path"]).resolve())
        self.assertEqual(data["arg"],"$(touch not-a-command)");self.assertFalse(Path(w["path"],"not-a-command").exists())
        self.assertTrue(result["process_id"])
    @case("P08","process.exact_launch_identity")
    def test_process_launch_retry_does_not_spawn_twice(self):
        w=self.worktree();self.patch("room-a",{"worktree":w["id"]})
        output=self.run_dir/"launch-count.txt"
        code="import pathlib,sys;p=pathlib.Path(sys.argv[1]);f=p.open('a');f.write('started\\n');f.close()"
        args={"argv":[sys.executable,"-c",code,str(output)],"operation_id":"same-launch"}
        a=self.a.act("process.launch",**args);b=self.a.act("process.launch",**args)
        self.assertEqual(a["process_id"],b["process_id"])
        self.assertEqual(self.call("process.wait",id=a["process_id"],timeout_seconds=5)["exit_code"],0)
        self.call("runtime.fence");self.assertEqual(output.read_text().splitlines(),["started"])
    @case("P08","git.restart_registry")
    def test_restart_reconciles_real_worktrees(self):
        w=self.worktree();self.restart(crash=True)
        s=self.call("git.status",worktree=w["id"]);self.assertEqual(s["head"],self.repo.head(w["path"]))
        self.assertEqual(self.repo.branch(w["path"]),"agent/a")
