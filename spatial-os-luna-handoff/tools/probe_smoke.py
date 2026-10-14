#!/usr/bin/env python3
"""Create a disposable private Xvfb and test the independent C probe, not the OS."""
import argparse,json,os,selectors,shutil,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def main():
    p=argparse.ArgumentParser();p.add_argument("--xi2",action="store_true");p.add_argument("--report",default="evidence/native-probe-report.json");a=p.parse_args()
    if not shutil.which("Xvfb"):print("BLOCKED: Xvfb missing",file=sys.stderr);return 2
    r,w=os.pipe();logpath=ROOT/"evidence/xvfb-probe.log";logpath.parent.mkdir(exist_ok=True)
    with logpath.open("wb") as log:
        proc=subprocess.Popen(["Xvfb","-displayfd",str(w),"-screen","0","1024x768x24","-nolisten","tcp"],pass_fds=(w,),stdout=log,stderr=log)
        os.close(w)
        try:
            with selectors.DefaultSelector() as sel:
                sel.register(r,selectors.EVENT_READ)
                if not sel.select(timeout=10):raise RuntimeError("Xvfb readiness timed out")
                display=os.read(r,64).decode().strip()
                if not display.isdigit():raise RuntimeError("Invalid Xvfb display response")
            os.environ["DISPLAY"]=":"+display;os.environ["SPATIAL_SMOKE_OWNED_DISPLAY"]="1"
            os.environ["SPATIAL_PROBE_XI2"]="1" if a.xi2 else "0"
            suite=unittest.defaultTestLoader.discover(str(ROOT/"tests/native"),top_level_dir=str(ROOT))
            result=unittest.TextTestRunner(verbosity=2).run(suite)
            ok=result.wasSuccessful() and not result.skipped and result.testsRun>0
            report={"kind":"native-test-witness-only","xi2":a.xi2,"status":"passed" if ok else "failed", "tests":result.testsRun,
                    "failures":len(result.failures),"errors":len(result.errors),"spatial_os_tested":False,"physical_gpu_tested":False}
            dest=Path(a.report);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(report,indent=2)+"\n")
            return 0 if ok else 1
        finally:
            os.close(r);proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
if __name__=="__main__":raise SystemExit(main())
