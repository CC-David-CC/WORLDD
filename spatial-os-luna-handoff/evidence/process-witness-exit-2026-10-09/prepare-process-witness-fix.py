import ast,hashlib,json,pathlib
root=pathlib.Path(__file__).resolve().parents[1]
source=(root/'tests/core/test_processes.py').read_text()
assert 'tests/core/test_processes.py' not in json.loads((root/'acceptance.lock.json').read_text())['files']
marker="if __name__ == '__main__':"
assert source.count(marker)==1
tests='''class ProcessWitnessTests(unittest.TestCase):
    def read_results(self, results, pids):
        from unittest.mock import patch
        with patch.object(Path, 'read_text', side_effect=results):
            return ProcessTests.gone(pids)

    def test_process_exit_during_stat_read_is_gone(self):
        import errno
        self.assertTrue(self.read_results([ProcessLookupError(errno.ESRCH, 'No such process')], [123]))

    def test_missing_stat_path_is_gone(self):
        self.assertTrue(self.read_results([FileNotFoundError()], [123]))

    def test_missing_process_does_not_hide_a_live_process(self):
        self.assertFalse(self.read_results([FileNotFoundError(), '124 (live) S 1'], [123,124]))

    def test_only_dead_process_states_are_gone(self):
        for state in ('Z','X'):
            self.assertTrue(self.read_results(['123 (name with ) parentheses) '+state+' 1'], [123]))
        for state in ('R','S','D','T','t','I'):
            self.assertFalse(self.read_results(['123 (live) '+state+' 1'], [123]))

    def test_unrelated_read_errors_are_not_process_exit(self):
        import errno
        for error in (PermissionError(errno.EACCES, 'denied'), OSError(errno.EIO, 'I/O error')):
            with self.assertRaises(type(error)):
                self.read_results([error], [123])

    def test_malformed_stat_is_not_process_exit(self):
        for value in ('','123 (name)'):
            with self.assertRaises(IndexError):
                self.read_results([value], [123])


'''
baseline=source.split(marker)[0]+tests+marker+'''
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProcessWitnessTests)
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
'''
old="try: state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]\n            except FileNotFoundError: continue"
new=old.replace('except FileNotFoundError:', 'except (FileNotFoundError, ProcessLookupError):')
assert source.count(old)==1
corrected=source.replace(old,new).replace(marker,tests+marker)
corrected=corrected.replace("    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())", "    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ProcessWitnessTests))\n    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())")
def bodies(text):
    node=next(n for n in ast.parse(text).body if isinstance(n,ast.ClassDef) and n.name=='ProcessTests')
    return {n.name:ast.dump(n,include_attributes=False) for n in node.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')}
assert bodies(source)==bodies(corrected) and len(bodies(source))==12
out=root/'local/process-witness-draft';out.mkdir(exist_ok=True)
for name,data in [('baseline.py',baseline),('test_processes.py',corrected)]:
    (out/name).write_text(data,newline='\n')
record={'old_test_bodies_unchanged':12,'new_witness_tests':6,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'candidate_sha256':hashlib.sha256(corrected.encode()).hexdigest()}
(out/'source-audit.json').write_text(json.dumps(record,indent=2)+'\n')
print(record)
