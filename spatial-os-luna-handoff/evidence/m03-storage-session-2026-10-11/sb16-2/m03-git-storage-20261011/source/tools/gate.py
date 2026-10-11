#!/usr/bin/env python3
"""Run a cumulative acceptance gate. Absent implementation/prerequisites are BLOCKED."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import time
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.guard import verify
PHASES=[f"P{i:02d}" for i in range(11)]

def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from flatten(item)
        else:yield item

def collect():
    loader=unittest.TestLoader()
    suite=loader.discover(str(ROOT/"tests/acceptance"),top_level_dir=str(ROOT))
    if loader.errors:raise RuntimeError("\n".join(loader.errors))
    tests=list(flatten(suite))
    if not tests:raise RuntimeError("No acceptance tests collected")
    for t in tests:
        fn=getattr(t,t._testMethodName)
        if getattr(fn,"phase",None) not in PHASES or not getattr(fn,"primitive",None):
            raise RuntimeError(f"Missing/invalid test metadata: {t.id()}")
    return sorted(tests,key=lambda t:(getattr(t,t._testMethodName).phase,t.id()))

class Result(unittest.TextTestResult):
    def __init__(self,*a,**k):super().__init__(*a,**k);self.rows=[];self.starts={};self.outcomes={}
    def startTest(self,test):self.starts[test.id()]=time.monotonic();self.outcomes[test.id()]="passed";super().startTest(test)
    def addFailure(self,test,err):self.outcomes[test.id()]="failed";super().addFailure(test,err)
    def addError(self,test,err):self.outcomes[test.id()]="error";super().addError(test,err)
    def addSkip(self,test,reason):self.outcomes[test.id()]="skipped";super().addSkip(test,reason)
    def addExpectedFailure(self,test,err):self.outcomes[test.id()]="expected-failure";super().addExpectedFailure(test,err)
    def addUnexpectedSuccess(self,test):self.outcomes[test.id()]="unexpected-success";super().addUnexpectedSuccess(test)
    def addSubTest(self,test,subtest,err):
        if err is not None:self.outcomes[test.id()]="failed"
        super().addSubTest(test,subtest,err)
    def stopTest(self,test):
        fn=getattr(test,test._testMethodName)
        self.rows.append({"test":test.id(),"phase":fn.phase,"primitive":fn.primitive,"status":self.outcomes[test.id()],
                          "seconds":round(time.monotonic()-self.starts[test.id()],6)})
        super().stopTest(test)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--through",choices=PHASES,default="P10")
    p.add_argument("--sut",help="JSON argv array for real spatial-test-host")
    p.add_argument("--case",help="substring filter; DIAGNOSTIC ONLY, never a completed phase")
    p.add_argument("--list",action="store_true")
    p.add_argument("--report",default="evidence/last-target-run.json")
    p.add_argument("--failfast",action="store_true")
    a=p.parse_args(argv)
    try:tests=collect()
    except Exception as e:print(f"BLOCKED: {e}",file=sys.stderr);return 2
    if a.list:
        for t in tests:
            fn=getattr(t,t._testMethodName)
            if fn.phase<=a.through:print(f"{fn.phase}\t{fn.primitive}\t{t.id()}")
        return 0
    problems=verify(ROOT,[t.id() for t in tests])
    if problems:print("BLOCKED: baseline integrity\n"+"\n".join(problems),file=sys.stderr);return 2
    command=a.sut or os.environ.get("SPATIAL_SUT")
    if not command:
        print("BLOCKED: no production SUT. Supply --sut '[\"/absolute/path/spatial-test-host\"]'. Harness self-tests do not satisfy this gate.",file=sys.stderr);return 2
    try:
        args=json.loads(command)
        if not isinstance(args,list) or not args or not all(isinstance(x,str) and x for x in args):raise ValueError("expected nonempty string array")
    except (ValueError,TypeError) as e:print(f"BLOCKED: invalid SUT command: {e}",file=sys.stderr);return 2
    if a.through>="P04" and not os.environ.get("SPATIAL_TEST_DISPLAY"):
        print("BLOCKED: set SPATIAL_TEST_DISPLAY to an explicitly dedicated test display; never default to the operator's desktop",file=sys.stderr);return 2
    if os.environ.get("SPATIAL_TEST_DISPLAY"):os.environ["DISPLAY"]=os.environ["SPATIAL_TEST_DISPLAY"]
    os.environ["SPATIAL_SUT"]=json.dumps(args)
    selected=[t for t in tests if getattr(t,t._testMethodName).phase<=a.through and (not a.case or a.case in t.id())]
    if not selected:print("BLOCKED: filter selected zero tests",file=sys.stderr);return 2
    result=unittest.TextTestRunner(verbosity=2,resultclass=Result,failfast=a.failfast).run(unittest.TestSuite(selected))
    ok=result.wasSuccessful() and not result.skipped and not result.expectedFailures and result.testsRun==len(selected)
    report={"kind":"production-acceptance","through":a.through,"diagnostic_only":bool(a.case),
            "status":"passed" if ok else "failed","utc":datetime.now(timezone.utc).isoformat(),
            "selected":len(selected),"executed":result.testsRun,"total_baseline":len(tests),
            "not_selected":len(tests)-len(selected),"tests":result.rows,
            "hardware_certified":False,"sut_argv":args}
    dest=Path(a.report);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(report,indent=2)+"\n")
    print(f"{'DIAGNOSTIC' if a.case else 'GATE'} {a.through}: {report['status'].upper()} ({result.testsRun}/{len(selected)} executed); report={dest}")
    return 0 if ok else 1
if __name__=="__main__":raise SystemExit(main())
