"""Real Git is an external oracle. Everything is confined to a unique test directory."""
import os
from pathlib import Path
import subprocess

class GitRepo:
    def __init__(self,root:Path,empty_initial_commit:bool=False):
        self.path=Path(root)/"source-repo";self.path.mkdir(parents=True)
        self.env=os.environ.copy()
        self.env.update({"GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":os.devnull,"GIT_TERMINAL_PROMPT":"0",
                         "GIT_AUTHOR_NAME":"Spatial Test","GIT_AUTHOR_EMAIL":"spatial-test@example.invalid",
                         "GIT_COMMITTER_NAME":"Spatial Test","GIT_COMMITTER_EMAIL":"spatial-test@example.invalid"})
        # Prevent inherited repository overrides from redirecting the fixture into a real checkout.
        for k in list(self.env):
            if k in {"GIT_DIR","GIT_WORK_TREE","GIT_INDEX_FILE","GIT_COMMON_DIR","GIT_OBJECT_DIRECTORY","GIT_ALTERNATE_OBJECT_DIRECTORIES","GIT_CONFIG_COUNT"} or k.startswith(("GIT_CONFIG_KEY_","GIT_CONFIG_VALUE_")):
                self.env.pop(k,None)
        self.run("init","-b","main")
        if empty_initial_commit:
            self.run("commit","--allow-empty","-m","initial empty project")
        else:
            self.write("README.txt","base\n");self.run("add","--","README.txt");self.run("commit","-m","initial")
    def run(self,*args,cwd=None,check=True):
        p=subprocess.run(["git","-c","core.hooksPath=/dev/null","-C",str(cwd or self.path),*args],
                         env=self.env,text=True,capture_output=True,timeout=15)
        if check and p.returncode:
            raise AssertionError(f"Git {' '.join(args)}: {p.stderr}")
        return p.stdout.rstrip("\n") if check else p
    def write(self,name,content,cwd=None):
        p=Path(cwd or self.path)/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,encoding="utf-8")
    def head(self,cwd=None):return self.run("rev-parse","HEAD",cwd=cwd)
    def branch(self,cwd=None):return self.run("symbolic-ref","--short","HEAD",cwd=cwd)
