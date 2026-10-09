import runpy,sys,unittest
scope=runpy.run_path(sys.argv.pop(1),run_name="counterexample")
suite=unittest.TestSuite([scope["SupervisorRegistryTests"]("test_owner_binds_exact_registration_through_production_client")])
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
