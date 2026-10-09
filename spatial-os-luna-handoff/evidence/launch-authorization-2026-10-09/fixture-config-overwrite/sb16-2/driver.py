import runpy,sys,unittest
scope=runpy.run_path(sys.argv.pop(1),run_name="diagnostic")
original=scope["LaunchAuthorizationTests"]
class Diagnostic(original):
    def test_configuration_snapshot(self):
        first=self.config_file(1);original=first.read_bytes();second=self.config_file(2)
        self.assertEqual(first.read_bytes(),original,"Another client configuration overwrote the first launch input")
suite=unittest.TestSuite([Diagnostic("test_configuration_snapshot")])
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
