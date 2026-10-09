"""Witness the absent launch journal in the qualified predecessor, without a mock."""
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(sys.argv.pop(1))/'tests/core'))
import test_authority_process_scope as existing

class DurabilityBaseline(existing.ProcessScopeTests):
    def test_child_must_find_durable_launch_record_before_execution(self):
        journal=self.files/'journal';journal.mkdir(mode=0o700)
        result=self.files/'observed';proc=self.probe()
        self.launch(proc, f'import pathlib,time;p=pathlib.Path({str(result)!r});t=p.with_suffix(".tmp");t.write_text(str(pathlib.Path({str(journal/"launch.db")!r}).exists()));t.replace(p);time.sleep(30)')
        self.wait_file(result)
        self.stop(proc)
        self.assertEqual(result.read_text(),'True','Predecessor released execution without a durable launch record')

if __name__=='__main__':
    suite=unittest.TestSuite(DurabilityBaseline(name) for name in DurabilityBaseline.__dict__ if name.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
