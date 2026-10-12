"""Manage a separately compiled X client; never obtains events from the SUT."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

class Probe:
    def __init__(self, evidence: Path, xi2=False, display=None):
        self.id="probe-"+uuid.uuid4().hex
        binary=Path(os.environ.get("SPATIAL_XPROBE",str(Path(__file__).resolve().parents[1]/"build/xprobe")))
        if not binary.is_file():
            raise RuntimeError("Independent native probe missing: run python tools/build_probe.py --xi2")
        self.binary=binary
        self.path=Path(evidence)/(self.id+".jsonl")
        self.log=self.path.open("w",encoding="utf-8")
        self.stderr=Path(evidence).joinpath(self.id+".stderr").open("wb")
        env=os.environ.copy()
        if display:env["DISPLAY"]=display
        self.env=env
        self.proc=subprocess.Popen([str(binary),"--id",self.id]+(["--xi2"] if xi2 else []),
                                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,
                                   text=True,bufsize=1,env=env)
        self.events=[];self.condition=threading.Condition();self.sequence=0;self.failure=None
        self.reader=threading.Thread(target=self._read,daemon=True);self.reader.start()
        try:self.ready=self.wait(lambda e:e.get("event")=="ready")
        except Exception:self.close();raise
        self.xid=self.ready["xid"]
    def _read(self):
        try:
            for line in self.proc.stdout:
                e=json.loads(line)
                with self.condition:
                    self.events.append(e);self.log.write(line);self.log.flush();self.condition.notify_all()
            with self.condition:
                self.failure=RuntimeError("X probe terminated; see its stderr");self.condition.notify_all()
        except Exception as e:
            with self.condition:self.failure=e;self.condition.notify_all()
    def wait(self,predicate,after=0,timeout=5):
        end=time.monotonic()+timeout
        with self.condition:
            while True:
                for e in self.events[after:]:
                    if predicate(e):return e
                if self.failure:raise self.failure
                remaining=end-time.monotonic()
                if remaining<=0:raise AssertionError(f"No matching native event; observed {self.events[after:]}")
                self.condition.wait(remaining)
    def command(self,op,*args):
        self.sequence+=1
        seq=self.sequence
        self.proc.stdin.write(" ".join([op,str(seq),*(str(a) for a in args)])+"\n");self.proc.stdin.flush()
        return self.wait(lambda e:e.get("event")=="ack" and e.get("seq")==seq)
    def geometry(self):
        self.command("geometry")
        return self.wait(lambda e:e.get("event")=="geometry" and e.get("seq")==self.sequence)
    def input_events(self,after=0):
        self.command("sync")
        with self.condition:
            return [dict(e) for e in self.events[after:] if e["event"] in ("key","button","motion","focus")]
    def capture(self,target,path):
        subprocess.run([str(self.binary),"--capture-window",str(target),"--output",str(path)],
                       check=True,timeout=10,env=self.env,capture_output=True)
    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=2)
        self.reader.join(timeout=2)
        for f in (self.proc.stdin,self.proc.stdout,self.stderr,self.log):
            f.close()
