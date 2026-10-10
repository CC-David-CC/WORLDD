import json,os,sys,time,unittest
from pathlib import Path
out=Path(sys.argv.pop(1));source=Path(sys.argv.pop(1))
sys.path.insert(0,str(source/'tests/core'))
import test_identity_participant_runtime as t
(out/'test-pid').write_text(str(os.getpid()))
deadline=time.monotonic()+25
while not (out/'trace-ready').exists():
    if time.monotonic()>deadline:raise RuntimeError('Tracer not ready')
    time.sleep(.01)
events=[];results=[]
for attempt in range(1,21):
    directory=out/f'attempt-{attempt:02d}';directory.mkdir()
    os.environ['SPATIAL_SECURITY_EVIDENCE']=str(directory/'native')
    class TimedResult(unittest.TextTestResult):
        def record(self,event,test):
            events.append(dict(attempt=attempt,event=event,test=test.id(),monotonic_ns=time.monotonic_ns()))
            (out/'case-timing.json').write_text(json.dumps(events,indent=2)+'\n')
        def startTest(self,test):self.record('start',test);super().startTest(test)
        def stopTest(self,test):super().stopTest(test);self.record('stop',test)
    suite=unittest.TestSuite([t.ParticipantRuntimeTests('test_real_barrier_waits_for_explicit_application_acknowledgement')])
    assert suite.countTestCases()==1
    with (directory/'suite.log').open('w') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=TimedResult).run(suite)
    results.append(dict(attempt=attempt,passed=result.wasSuccessful(),tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped)))
    (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(results[-1],flush=True)
    if not result.wasSuccessful():break
raise SystemExit(0 if all(row['passed'] for row in results) else 1)
