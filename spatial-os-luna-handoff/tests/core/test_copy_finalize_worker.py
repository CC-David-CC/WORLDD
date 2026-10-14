"""Actual confined finalizer validation. Run only on the two qualification hosts."""
import fcntl
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

WORKER=Path(sys.argv.pop(1)).resolve()
sys.path.insert(0,str(WORKER.parent/'python'))
from spatial.workspace.v1 import copy_worker_pb2 as worker
import copy_pb2 as copy
BWRAP='/usr/local/libexec/spatial-bwrap'

class CopyFinalizeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertIn(socket.gethostname().split('.')[0],('sb16','sb16-2'))
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.repository=self.root/'repo.git';self.project=self.root/'project'
        seed=self.root/'seed';self.git('init',seed)
        (seed/'initial').write_bytes(b'actual initial history')
        self.git('-C',seed,'add','initial');self.git('-C',seed,'commit','-m','initial history')
        self.git('clone','--no-hardlinks','--bare',seed,self.repository)
        self.git('--git-dir='+str(self.repository),'worktree','add','-b','copy-source',self.project)
        (self.project/'tracked').write_bytes(b'committed bytes')
        self.git('-C',self.project,'add','tracked');self.git('-C',self.project,'commit','-m','actual history')
        (self.project/'tracked').write_bytes(b'staged bytes');self.git('-C',self.project,'add','tracked')
        (self.project/'tracked').write_bytes(b'dirty bytes');(self.project/'untracked').write_bytes(b'untracked bytes')
        self.request=worker.WorkerRequest(protocol_version=2,maximum_bytes=32*2**20,maximum_entries=10000)
        job=self.request.finalization
        job.classes[:]=[copy.PROJECT_TREE,copy.GIT_HISTORY]
        job.source_repository_path=str(self.repository);job.destination_repository_path='/fresh/repo.git'
        job.destination_project_path='/fresh/project';job.destination_admin_id='f'*32
        job.derive_head_from_snapshot=True
    def git(self,*arguments):
        result=subprocess.run(['git',*map(str,arguments)],capture_output=True,timeout=15,
            env=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',
                GIT_AUTHOR_NAME='Copy Witness',GIT_AUTHOR_EMAIL='copy@example.invalid',
                GIT_COMMITTER_NAME='Copy Witness',GIT_COMMITTER_EMAIL='copy@example.invalid'))
        self.assertEqual(result.returncode,0,result.stderr);return result.stdout
    def run_finalizer(self):
        for path in self.repository.rglob('*'):
            if path.is_file():self.assertEqual(path.stat().st_nlink,1,'Fixture must represent independent staged Git objects: '+str(path))
        fd=os.memfd_create('finalizer-regression',os.MFD_ALLOW_SEALING)
        try:
            os.write(fd,self.request.SerializeToString());os.lseek(fd,0,os.SEEK_SET)
            fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
            command=[BWRAP,'--unshare-all','--unshare-user','--disable-userns','--die-with-parent',
                '--new-session','--cap-drop','ALL','--clearenv']
            for directory in ('/usr/bin','/usr/lib','/usr/lib64','/usr/share'):
                command+=['--ro-bind',directory,directory]
            command+=['--symlink','usr/bin','/bin','--symlink','usr/lib','/lib',
                '--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
                '--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/nonexistent',
                '--setenv','GIT_CONFIG_NOSYSTEM','1','--setenv','GIT_CONFIG_GLOBAL','/dev/null',
                '--setenv','LC_ALL','C','--ro-bind',str(WORKER),'/finalize-worker',
                '--dir','/domains','--bind',str(self.project),'/domains/1',
                '--bind',str(self.repository),'/domains/2','--ro-bind-data',str(fd),'/job.pb']
            for variable in ('ASAN_OPTIONS','UBSAN_OPTIONS'):
                if variable in os.environ:command+=['--setenv',variable,os.environ[variable]]
            command+=['--remount-ro','/','--','/finalize-worker']
            result=subprocess.run(command,pass_fds=(fd,),capture_output=True,timeout=30)
        finally:os.close(fd)
        self.assertEqual(result.returncode,0,result.stderr)
        for marker in (b'AddressSanitizer',b'LeakSanitizer',b'UndefinedBehaviorSanitizer',b'runtime error:'):
            self.assertNotIn(marker,result.stderr)
        self.assertGreaterEqual(len(result.stdout),5,result.stderr)
        size=int.from_bytes(result.stdout[:4],'big');self.assertLessEqual(size,16384)
        self.assertEqual(size+4,len(result.stdout))
        reply=worker.WorkerResult.FromString(result.stdout[4:]);self.assertEqual(reply.protocol_version,2)
        return reply,result.stderr
    def test_valid_finalization_preserves_dirty_staged_untracked_and_history(self):
        head=self.git('-C',self.project,'rev-parse','HEAD').decode().strip()
        staged=self.git('-C',self.project,'diff','--cached','--binary')
        unstaged=self.git('-C',self.project,'diff','--binary')
        history=self.git('-C',self.project,'rev-list','--all')
        index=self.git('-C',self.project,'ls-files','--stage','-z')
        reply,diagnostics=self.run_finalizer()
        self.assertEqual(reply.WhichOneof('outcome'),'finalized',diagnostics)
        self.assertEqual(reply.finalized.head,head);self.assertEqual(reply.finalized.branch,'copy-source')
        self.assertEqual({item.data_class for item in reply.finalized.domains},{copy.PROJECT_TREE,copy.GIT_HISTORY})
        self.assertEqual((self.project/'tracked').read_bytes(),b'dirty bytes')
        self.assertEqual((self.project/'untracked').read_bytes(),b'untracked bytes')
        explicit=['--git-dir='+str(self.repository/'worktrees'/('f'*32)),'--work-tree='+str(self.project)]
        self.assertEqual(self.git(*explicit,'diff','--cached','--binary'),staged)
        self.assertEqual(self.git(*explicit,'diff','--binary'),unstaged)
        self.assertEqual(self.git(*explicit,'rev-list','--all'),history)
        self.assertEqual(self.git(*explicit,'ls-files','--stage','-z'),index)
    def test_mixed_finalization_state_export_rejected_before_git_or_tree_effects(self):
        self.request.state_export.key='otherwise-valid-key'
        self.request.state_export.schema_id='spatial.application.quiesced-files'
        self.request.state_export.schema_version=1;self.request.state_export.subdomains[:]=[1]
        before={str(path.relative_to(self.root)):path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        reply,diagnostics=self.run_finalizer()
        self.assertEqual(reply.WhichOneof('outcome'),'failure')
        self.assertIn(b'Unsupported bounded finalization request',diagnostics)
        after={str(path.relative_to(self.root)):path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before,after)
    def test_finalizer_depth_128_succeeds(self):
        current=self.project
        for unused in range(128):current=current/'d';current.mkdir()
        reply,diagnostics=self.run_finalizer()
        self.assertEqual(reply.WhichOneof('outcome'),'finalized',diagnostics)
    def test_finalizer_depth_129_rejected(self):
        current=self.project
        for unused in range(129):current=current/'d';current.mkdir()
        reply,diagnostics=self.run_finalizer()
        self.assertEqual(reply.WhichOneof('outcome'),'failure')
        self.assertIn(b'Finalization directory depth exceeded',diagnostics)

if __name__=='__main__':unittest.main()
