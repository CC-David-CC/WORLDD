import json
import os
from pathlib import Path
import sys
from harness.base import Acceptance,case
from harness.git_fixture import GitRepo
from harness.oracles import pose

class GitTests(Acceptance):
    def setUp(self):
        super().setUp();self.room();self.a=self.actor();self.repo=GitRepo(self.run_dir)
        self.backing=self.call("git.repo.import",id="repo",path=str(self.repo.path))
    def worktree(self,id="work-a",branch="agent/a",operation_id="create-a"):
        return self.call("git.worktree.create",repo="repo",id=id,branch=branch,start="main",operation_id=operation_id)
    @case("P08","git.real_worktree")
    def test_create_is_a_real_git_worktree(self):
        w=self.worktree();p=Path(w["path"])
        self.assertEqual(self.repo.run("rev-parse","--is-bare-repository",cwd=self.backing["path"]),"true")
        self.assertTrue(Path(self.backing["path"]).is_relative_to(self.state_dir))
        self.assertTrue(p.is_dir());self.assertEqual(self.repo.branch(p),"agent/a");self.assertEqual(self.repo.head(p),self.repo.head())
        listed=self.repo.run("worktree","list","--porcelain",cwd=self.backing["path"]);self.assertIn(str(p),listed)
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
        self.assertEqual(self.repo.run("worktree","list","--porcelain",cwd=self.backing["path"]).count("branch refs/heads/agent/a"),1)
    @case("P08","git.invalid_ref")
    def test_invalid_branch_name_fails_without_side_effect(self):
        before=self.repo.run("worktree","list","--porcelain",cwd=self.backing["path"])
        for branch in ("../escape","bad..name","a.lock","--orphan=x"):
            self.error("INVALID_ARGUMENT","git.worktree.create",repo="repo",id="bad",branch=branch,start="main",operation_id="bad-"+branch)
        self.assertEqual(self.repo.run("worktree","list","--porcelain",cwd=self.backing["path"]),before)
    @case("P08","git.idempotency")
    def test_retry_same_operation_does_not_duplicate_worktree(self):
        a=self.worktree();b=self.worktree();self.assertEqual(a,b)
        self.assertEqual(self.repo.run("worktree","list","--porcelain",cwd=self.backing["path"]).count("branch refs/heads/agent/a"),1)
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
        self.assertFalse(Path(w["path"]).exists());self.assertEqual(self.repo.run("rev-parse","agent/a",cwd=self.backing["path"]),self.repo.head())
    @case("P08","git.permission")
    def test_actor_without_git_grant_cannot_create_worktree(self):
        b=self.actor("readonly",grants=["navigate"])
        self.actor_error("FORBIDDEN",b,"git.worktree.create",repo="repo",id="denied",branch="denied",start="main",operation_id="denied")
        self.assertEqual(self.repo.run("worktree","list","--porcelain",cwd=self.backing["path"]).count("worktree "),1)
    rights=["workspace.select","files.read","files.write","process.launch"]
    def workspace(self,w,ident="workspace-a",actor=None,rights=None):
        actor=actor or self.a
        value=self.call("workspace.create",id=ident,worktree=w["id"],policy=self.rights)
        self.call("workspace.member.set",id=ident,actor=actor.id,rights=self.rights if rights is None else rights)
        self.call("workspace.available",id=ident,available=True)
        actor.act("workspace.select",id=ident)
        return value
    def launch(self,actor,argv,operation_id="launch",profile="development"):
        result=actor.act("process.launch",argv=argv,operation_id=operation_id,profile=profile)
        completed=self.call("process.wait",id=result["process_id"],timeout_seconds=5)
        self.assertEqual(completed["exit_code"],0)
        return result
    @case("P08","process.workspace_cwd")
    def test_launched_process_runs_in_selected_workspaces_project(self):
        w=self.worktree();self.workspace(w)
        output=Path(w["path"])/"cwd-result.json"
        script=Path(w["path"])/"witness.py"
        script.write_text("import json,os,sys,pathlib\npathlib.Path(sys.argv[1]).write_text(json.dumps({'cwd':os.getcwd(),'arg':sys.argv[2]}))\n")
        result=self.launch(self.a,[sys.executable,"/project/witness.py","/project/cwd-result.json","$(touch not-a-command)"],"launch-cwd")
        data=json.loads(output.read_text());self.assertEqual(data["cwd"],"/project")
        self.assertEqual(data["arg"],"$(touch not-a-command)");self.assertFalse(Path(w["path"],"not-a-command").exists())
        self.assertTrue(result["process_id"])
    @case("P08","process.exact_launch_identity")
    def test_process_launch_retry_does_not_spawn_twice(self):
        w=self.worktree();self.workspace(w)
        output=Path(w["path"])/"launch-count.txt"
        code="import pathlib,sys;p=pathlib.Path(sys.argv[1]);f=p.open('a');f.write('started\\n');f.close()"
        args={"argv":[sys.executable,"-c",code,"/project/launch-count.txt"],"operation_id":"same-launch","profile":"development"}
        a=self.a.act("process.launch",**args);b=self.a.act("process.launch",**args)
        self.assertEqual(a["process_id"],b["process_id"])
        self.assertEqual(self.call("process.wait",id=a["process_id"],timeout_seconds=5)["exit_code"],0)
        self.call("runtime.fence");self.assertEqual(output.read_text().splitlines(),["started"])
    @case("P08","git.restart_registry")
    def test_restart_reconciles_real_worktrees(self):
        w=self.worktree();self.restart(crash=True)
        s=self.call("git.status",worktree=w["id"]);self.assertEqual(s["head"],self.repo.head(w["path"]))
        self.assertEqual(self.repo.branch(w["path"]),"agent/a")
    @case("P08","workspace.explicit_context")
    def test_workspace_creation_availability_and_selection_are_separate(self):
        w=self.worktree();before=self.call("world.snapshot")["entities"]
        ws=self.call("workspace.create",id="workspace-a",worktree=w["id"],policy=self.rights)
        identity=self.call("runtime.identities");broker=identity["workspaced_pid"]
        self.assertGreater(broker,1);self.assertNotIn(broker,[identity["worldd_pid"],identity["spatialwm_pid"],os.getpid()])
        self.assertTrue(Path(f"/proc/{broker}").is_dir());self.assertEqual(os.getpgid(broker),self.client.proc.pid)
        self.assertEqual(ws["worktree"],w["id"]);self.assertNotIn("room",ws);self.assertNotIn("default_room",ws)
        self.assertIsNone(self.a.info()["workspace"])
        self.call("workspace.member.set",id=ws["id"],actor=self.a.id,rights=self.rights)
        self.actor_error("UNAVAILABLE",self.a,"workspace.select",id=ws["id"])
        self.call("workspace.available",id=ws["id"],available=True)
        self.assertIsNone(self.a.info()["workspace"])
        self.a.act("workspace.select",id=ws["id"]);self.assertEqual(self.a.info()["workspace"],ws["id"])
        self.assertEqual(self.call("world.snapshot")["entities"],before)
    @case("P08","workspace.access")
    def test_world_presence_and_global_grants_do_not_grant_workspace_access(self):
        w=self.worktree();self.workspace(w)
        b=self.actor("other")
        self.actor_error("FORBIDDEN",b,"workspace.select",id="workspace-a")
        self.assertIsNone(b.info()["workspace"])
        self.actor_error("NO_WORKSPACE",b,"process.launch",argv=[sys.executable,"-c","raise SystemExit(0)"],operation_id="denied",profile="development")
    @case("P08","workspace.room_independence")
    def test_portal_entry_keeps_selection_and_does_not_restart_runtime(self):
        w=self.worktree();self.workspace(w);self.room("other")
        self.obj("door",kind="portal",pose=pose([0,0,-.1]),size=[2,2],destination={"room":"other","pose":pose()})
        identity=self.call("runtime.identities")
        self.a.act("input",axes=[0,0,-1,0,0,0]);self.advance(.2)
        self.assertEqual(self.a.info()["room"],"other");self.assertEqual(self.a.info()["workspace"],"workspace-a")
        self.launch(self.a,[sys.executable,"-c","from pathlib import Path;Path('from-other-room').write_text('same workspace')"])
        self.assertEqual(Path(w["path"],"from-other-room").read_text(),"same workspace")
        self.assertEqual(self.call("runtime.identities"),identity)
    @case("P08","workspace.selection_clear")
    def test_unavailability_and_revocation_clear_selection(self):
        w=self.worktree();self.workspace(w)
        self.call("workspace.available",id="workspace-a",available=False);self.assertIsNone(self.a.info()["workspace"])
        self.call("workspace.available",id="workspace-a",available=True);self.assertIsNone(self.a.info()["workspace"])
        self.a.act("workspace.select",id="workspace-a")
        self.call("workspace.member.set",id="workspace-a",actor=self.a.id,rights=[])
        self.assertIsNone(self.a.info()["workspace"])
        self.actor_error("FORBIDDEN",self.a,"workspace.select",id="workspace-a")
    @case("P08","workspace.private_git_metadata")
    def test_independent_workspaces_cannot_share_writable_git_metadata(self):
        a=self.worktree();b=self.worktree("work-b","agent/b","create-b")
        self.call("workspace.create",id="workspace-a",worktree=a["id"],policy=self.rights)
        self.error("IN_USE","workspace.create",id="same-directory",worktree=a["id"],policy=self.rights)
        self.error("IN_USE","workspace.create",id="shared-git-metadata",worktree=b["id"],policy=self.rights)
        self.error("IN_USE","git.worktree.remove",id=a["id"],operation_id="remove-bound")
        self.assertTrue(Path(a["path"]).is_dir())
    @case("P08","workspace.mount_isolation")
    def test_two_workspaces_use_same_project_path_in_distinct_mount_namespaces(self):
        a=self.worktree();self.workspace(a)
        backing=self.call("git.repo.import",id="repo-b",path=str(self.repo.path))
        b=self.call("git.worktree.create",repo="repo-b",id="work-b",branch="agent/b",start="main",operation_id="create-b")
        other=self.actor("other");self.workspace(b,"workspace-b",other)
        self.assertNotEqual(Path(self.backing["path"]).resolve(),Path(backing["path"]).resolve())
        code="""import json,os,pathlib,sys,time
pathlib.Path('same-name').write_text(sys.argv[1])
pathlib.Path('witness.tmp').write_text(json.dumps({'cwd':os.getcwd(),'mnt':os.readlink('/proc/self/ns/mnt')}))
pathlib.Path('witness.tmp').replace('witness.json')
deadline=time.monotonic()+10
while not pathlib.Path('release').exists():
    if time.monotonic()>deadline: raise SystemExit(20)
    time.sleep(.01)
"""
        processes=[]
        for actor,w,name in [(self.a,a,"first"),(other,b,"second")]:
            processes.append(actor.act("process.launch",argv=[sys.executable,"-c",code,name],operation_id="launch-"+name,profile="development"))
        for w in [a,b]:self.until(lambda:Path(w["path"],"witness.json").is_file(),bool)
        for w,name in [(a,"first"),(b,"second")]:
            self.assertEqual(Path(w["path"],"same-name").read_text(),name)
        x=json.loads(Path(a["path"],"witness.json").read_text());y=json.loads(Path(b["path"],"witness.json").read_text())
        for w in [a,b]:Path(w["path"],"release").touch()
        for process in processes:self.assertEqual(self.call("process.wait",id=process["process_id"],timeout_seconds=5)["exit_code"],0)
        self.assertEqual(x["cwd"],"/project");self.assertEqual(y["cwd"],"/project")
        self.assertNotEqual(x["mnt"],y["mnt"])
        self.assertNotIn(os.readlink('/proc/self/ns/mnt'),[x["mnt"],y["mnt"]])
        self.assertEqual(Path(a["path"],"same-name").read_text(),"first")
    @case("P08","process.filesystem_boundary")
    def test_child_process_cannot_read_or_write_outside_workspace(self):
        w=self.worktree();self.workspace(w)
        canary=self.run_dir/"outside-secret";canary.write_text("outside authority")
        target=self.run_dir/"outside-write"
        code="""import pathlib,sys
try: pathlib.Path(sys.argv[1]).read_text()
except OSError: pass
else: raise SystemExit(10)
try: pathlib.Path(sys.argv[2]).write_text('escaped')
except OSError: pass
else: raise SystemExit(11)
status=pathlib.Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\\t1' in status
"""
        self.repo.write("child.py",code,w["path"])
        parent="import subprocess,sys;raise SystemExit(subprocess.call([sys.executable,'/project/child.py',*sys.argv[1:]]))"
        self.launch(self.a,[sys.executable,"-c",parent,str(canary),str(target)])
        self.assertEqual(canary.read_text(),"outside authority");self.assertFalse(target.exists())
    @case("P08","process.authority_intersection")
    def test_application_profile_and_actor_rights_both_limit_writes(self):
        w=self.worktree();self.workspace(w)
        code="""from pathlib import Path
assert Path('README.txt').read_text()=='base\\n'
try: Path('README.txt').write_text('unauthorized')
except OSError: pass
else: raise SystemExit(12)
"""
        self.launch(self.a,[sys.executable,"-c",code],"read-only-profile",profile="read-only")
        self.call("workspace.member.set",id="workspace-a",actor=self.a.id,rights=["workspace.select","files.read","process.launch"])
        self.launch(self.a,[sys.executable,"-c",code],"read-only-actor",profile="development")
        self.assertEqual(Path(w["path"],"README.txt").read_text(),"base\n")
    @case("P08","process.git_in_namespace")
    def test_ordinary_git_in_workspace_updates_real_worktree(self):
        w=self.worktree();self.workspace(w)
        code="""from pathlib import Path
import subprocess
Path('README.txt').write_text('from confined Git\\n')
subprocess.run(['git','add','--','README.txt'],check=True)
subprocess.run(['git','-c','user.name=Workspace Witness','-c','user.email=witness@example.invalid','commit','-m','confined commit'],check=True)
"""
        self.launch(self.a,[sys.executable,"-c",code])
        self.assertEqual(Path(w["path"],"README.txt").read_text(),"from confined Git\n")
        self.assertNotEqual(self.repo.head(w["path"]),w["head"])
        self.assertEqual(self.call("git.status",worktree=w["id"])["head"],self.repo.head(w["path"]))
    @case("P08","process.retry_workspace_binding")
    def test_launch_retry_cannot_change_workspace_context(self):
        a=self.worktree();self.workspace(a)
        self.call("git.repo.import",id="repo-b",path=str(self.repo.path))
        b=self.call("git.worktree.create",repo="repo-b",id="work-b",branch="agent/b",start="main",operation_id="create-b")
        argv=[sys.executable,"-c","from pathlib import Path;Path('launched').write_text('once')"]
        self.launch(self.a,argv,"same-operation")
        self.workspace(b,"workspace-b")
        self.actor_error("OPERATION_CONFLICT",self.a,"process.launch",argv=argv,operation_id="same-operation",profile="development")
        self.assertFalse(Path(b["path"],"launched").exists())
