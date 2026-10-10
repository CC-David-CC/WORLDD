"""Repeat the unchanged failing assertion while retaining private fixture logs."""
import os
from pathlib import Path
import shutil
import unittest
import test_control_owner_binding as original

evidence = Path(os.environ['SPATIAL_BINDING_STARTUP_EVIDENCE'])
evidence.mkdir(exist_ok=False)


class StartupCase(original.SupervisorBindingTests):
    iteration = 0

    def setUp(self):
        super().setUp()
        StartupCase.iteration += 1
        target = evidence / str(StartupCase.iteration)
        target.mkdir()

        def preserve_logs():
            for label, root in (('supervisor', self.root), ('issuer', self.issuer.root)):
                for path in root.glob('*.log'):
                    shutil.copyfile(path, target / (label + '-' + path.name))

        self.addCleanup(preserve_logs)


suite = unittest.TestSuite(StartupCase('test_production_supervisor_returns_exact_owned_binding_without_stopping_child')
    for _ in range(10))
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
