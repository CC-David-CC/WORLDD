#!/usr/bin/env python3
"""Exercise local staging validation and actual receive-hook rejection."""
import os, shutil, subprocess, tempfile, unittest
from pathlib import Path

GUARD=Path(__file__).with_name('repository_data_guard.py').resolve()

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name); self.work=self.base/'work'; self.remote=self.base/'remote.git'
        self.run_git('init','--initial-branch=main',str(self.work)); self.run_git('init','--bare',str(self.remote))
        self.run_git('-C',str(self.work),'config','user.name','Test'); self.run_git('-C',str(self.work),'config','user.email','test@localhost')
        shutil.copyfile(GUARD,self.remote/'hooks'/'repository_data_guard.py')
        hook=self.remote/'hooks'/'pre-receive'; hook.write_text('#!/bin/sh\nexec python3 "$(dirname "$0")/repository_data_guard.py" --receive\n'); hook.chmod(0o755)
        self.run_git('-C',str(self.work),'remote','add','origin',str(self.remote))
        self.add('src/main.cpp','int main() { return 0; }\n'); self.run_git('-C',str(self.work),'commit','-m','source'); self.run_git('-C',str(self.work),'push','origin','main')
    def tearDown(self): self.temp.cleanup()
    def run_git(self,*args,ok=True):
        r=subprocess.run(['git',*args],capture_output=True)
        if ok and r.returncode: self.fail(r.stderr.decode())
        return r
    def add(self,name,text):
        f=self.work/name; f.parent.mkdir(parents=True,exist_ok=True); f.write_text(text)
        self.run_git('-C',str(self.work),'add','--',name)
    def reject(self,name):
        self.add(name,'generated\n')
        local=subprocess.run(['python3',str(GUARD)],cwd=self.work,capture_output=True)
        self.assertNotEqual(local.returncode,0); self.assertIn(name,local.stderr.decode())
        self.run_git('-C',str(self.work),'commit','-m','forcibly staged generated data')
        r=self.run_git('-C',str(self.work),'push','origin','main',ok=False)
        self.assertNotEqual(r.returncode,0); self.assertIn('SOURCE POLICY REJECTED',r.stderr.decode())
    def test_evidence_rejected(self): self.reject('spatial-os-luna-handoff/evidence/run/result.json')
    def test_build_rejected(self): self.reject('spatial-os-luna-handoff/build-debug/CMakeCache.txt')
    def test_generated_bindings_rejected(self): self.reject('proto/world_pb2.py')
    def test_hidden_bad_ancestor_rejected(self):
        self.add('run-scratch/stdout.log','bad\n'); self.run_git('-C',str(self.work),'commit','-m','bad ancestor')
        self.run_git('-C',str(self.work),'rm','run-scratch/stdout.log'); self.run_git('-C',str(self.work),'commit','-m','clean tip')
        r=self.run_git('-C',str(self.work),'push','origin','main',ok=False)
        self.assertNotEqual(r.returncode,0); self.assertIn('SOURCE POLICY REJECTED',r.stderr.decode())
    def test_required_fixture_allowed(self):
        self.add('tests/fixtures/world.json','{"room":"entry"}\n'); self.run_git('-C',str(self.work),'commit','-m','fixture')
        self.run_git('-C',str(self.work),'push','origin','main')

if __name__=='__main__': unittest.main()
