#!/usr/bin/env python3
import importlib.util, subprocess, tempfile, unittest
from pathlib import Path
SCRIPTS=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('links',SCRIPTS/'configure_artifact_links.py'); links=importlib.util.module_from_spec(spec);spec.loader.exec_module(links)
class RoutingTests(unittest.TestCase):
    def test_legacy_output_is_external_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); root=base/'source'; (root/'spatial-os-luna-handoff').mkdir(parents=True); out=base/'artifacts'
            links.configure(root,out); links.configure(root,out)
            path=root/'spatial-os-luna-handoff/evidence/result.json';path.write_text('{}')
            self.assertEqual(path.resolve(),out/'spatial-os-luna-handoff/evidence/result.json')
    def test_existing_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); root=base/'source'; (root/'spatial-os-luna-handoff/evidence').mkdir(parents=True)
            marker=root/'spatial-os-luna-handoff/evidence/keep.txt'; marker.write_text('keep')
            with self.assertRaises(ValueError): links.configure(root,base/'artifacts')
            self.assertEqual(marker.read_text(),'keep')
    def test_command_runner_preserves_exit_outside_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); root=base/'source';root.mkdir();run=base/'artifacts/job'
            r=subprocess.run(['bash',str(SCRIPTS/'run-command.sh'),str(root),str(run),'bash','-c','exit 7'],capture_output=True)
            self.assertEqual(r.returncode,7);self.assertEqual((run/'command.exit').read_text().strip(),'7');self.assertTrue((run/'wrapper.exit').exists())
    def test_command_runner_rejects_checkout_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);run=root/'local/job'
            r=subprocess.run(['bash',str(SCRIPTS/'run-command.sh'),str(root),str(run),'true'],capture_output=True)
            self.assertEqual(r.returncode,65); self.assertFalse(run.exists())
    def test_sparse_cleanup_preserves_dirty_and_untracked_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def git(*args): return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE)
            git('init');git('config','user.name','Test');git('config','user.email','test@localhost')
            (root/'spatial-os-luna-handoff/evidence').mkdir(parents=True)
            (root/'source.cpp').write_text('original')
            (root/'spatial-os-luna-handoff/evidence/clean.json').write_text('{}')
            dirty=root/'spatial-os-luna-handoff/evidence/dirty.json'; dirty.write_text('{}')
            git('add','.');git('commit','-m','fixture')
            (root/'source.cpp').write_text('modified')
            dirty.write_text('modified evidence'); unique=root/'spatial-os-luna-handoff/evidence/unique.txt';unique.write_text('unique')
            git('sparse-checkout','set','--no-cone','/*','!/spatial-os-luna-handoff/evidence/')
            self.assertEqual((root/'source.cpp').read_text(),'modified')
            self.assertEqual(dirty.read_text(),'modified evidence');self.assertEqual(unique.read_text(),'unique')
            self.assertFalse((root/'spatial-os-luna-handoff/evidence/clean.json').exists())
    def test_artifact_links_are_ignored_by_repository_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); root=base/'source'; (root/'spatial-os-luna-handoff').mkdir(parents=True)
            (root/'.gitignore').write_bytes((SCRIPTS.parents[2]/'.gitignore').read_bytes())
            subprocess.check_call(['git','init',str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.check_call(['git','-C',str(root),'add','.gitignore'])
            links.configure(root,base/'artifacts')
            names=subprocess.check_output(['git','-C',str(root),'ls-files','--others','--exclude-standard']).decode()
            self.assertEqual(names,'')
if __name__=='__main__':unittest.main()
