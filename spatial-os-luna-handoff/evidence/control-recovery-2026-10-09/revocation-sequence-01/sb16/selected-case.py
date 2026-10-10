import json,os,sys,time,unittest
from pathlib import Path
out=Path(sys.argv.pop(1));source=Path(sys.argv.pop(1))
sys.path.insert(0,str(source/'tests/native'))
import test_authority_supervisor as t
(out/'test-pid').write_text(str(os.getpid()))
until=time.monotonic()+25
while not (out/'trace-ready').exists():
    if time.monotonic()>until: raise RuntimeError('Timing tracer did not become ready')
    time.sleep(.01)
names=[name for name in t.AuthoritySupervisorTests.__dict__ if name.startswith('test_')]
assert names[2]=='test_revocation_requires_child_acknowledgement_with_automatic_delivery'
assert len(names)==14
os.environ['SPATIAL_SECURITY_EVIDENCE']=str(out/'native')
events=[]
class TimedResult(unittest.TextTestResult):
    def record(self,event,test):
        events.append(dict(event=event,test=test.id(),monotonic_ns=time.monotonic_ns()))
        (out/'case-timing.json').write_text(json.dumps(events,indent=2)+'\n')
    def startTest(self,test):
        self.record('start',test);super().startTest(test)
    def stopTest(self,test):
        super().stopTest(test);self.record('stop',test)
with (out/'suite.log').open('w') as stream:
    result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=TimedResult).run(
        unittest.TestSuite(t.AuthoritySupervisorTests(name) for name in names))
results=[dict(passed=result.wasSuccessful(),tests=result.testsRun,failures=len(result.failures),
    errors=len(result.errors),skipped=len(result.skipped),declaration_order=names)]
(out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
print(results[0],flush=True)
raise SystemExit(0 if result.wasSuccessful() else 1)
