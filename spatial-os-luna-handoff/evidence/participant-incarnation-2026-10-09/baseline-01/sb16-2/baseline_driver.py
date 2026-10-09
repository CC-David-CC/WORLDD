import runpy,sys,unittest
scope=runpy.run_path(sys.argv.pop(1),run_name="counterexample")
suite=unittest.TestSuite([scope["IncarnationTests"]("test_exact_preassigned_incarnation_reaches_durable_activation")])
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
