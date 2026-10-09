import json,runpy,sys,unittest
scope=runpy.run_path(sys.argv.pop(1),run_name="diagnostic")
original=scope["OwnedParticipantTests"]
class Diagnostic(original):
    def test_independent_child_observes_execution_without_approval(self):
        proc=self.probe();view=self.prepared(proc);observed=self.root/'execution-witness'
        code=('import json,os,pathlib,time\n'
            f'p=pathlib.Path({str(observed)!r});t=p.with_suffix(".tmp")\n'
            't.write_text(json.dumps({"pid":os.getpid(),"cgroup":pathlib.Path("/proc/self/cgroup").read_text()}));t.replace(p);time.sleep(30)')
        reply=self.run_child(proc,code);self.assertTrue(reply.HasField('view'),reply)
        self.wait_file(observed);actual=json.loads(observed.read_text())
        self.assertIn(view.record.process.scope_name,actual['cgroup'])
        self.assertFalse(self.read_record().HasField('authorization'))
        print(json.dumps(dict(confirmed_missing_gate=True,launch_id=view.record.process.launch_id.hex(),child=actual)),flush=True)
        self.stopped(proc)
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([Diagnostic('test_independent_child_observes_execution_without_approval')])).wasSuccessful())
