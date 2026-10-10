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
results=[]
for index in range(1,11):
    directory=out/f'attempt-{index:02d}';directory.mkdir()
    os.environ['SPATIAL_SECURITY_EVIDENCE']=str(directory/'native')
    with (directory/'run.log').open('w') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.TestSuite([
            t.AuthoritySupervisorTests('test_revocation_requires_child_acknowledgement_with_automatic_delivery')]))
    results.append(dict(index=index,passed=result.wasSuccessful(),tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped)))
    (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(f'attempt {index}: '+('passed' if result.wasSuccessful() else 'failed'),flush=True)
    if not result.wasSuccessful(): break
raise SystemExit(0 if all(item['passed'] for item in results) else 1)
