#!/usr/bin/env python3
"""Check borrowed workspace setup reaches the configured production broker factory.

Pass identityd, spatial-issuer-client, and worldd paths for the imported fixtures.
This regression does not launch those programs.
"""
from pathlib import Path
import tempfile
import unittest
import test_workspace_geometry_native_crosshost as geometry_fixture
import test_cabinet_native_crosshost as cabinet_fixture


class WorkspaceNativeFactoryTests(unittest.TestCase):
    def check_factory(self, fixture_type):
        class ReachedFactory(Exception):
            pass

        fixture = fixture_type('runTest')
        calls = []
        def factory(owner, state, config=None):
            calls.append((owner, state, config))
            raise ReachedFactory

        fixture.broker_factory = factory
        with tempfile.TemporaryDirectory() as directory:
            fixture.root = Path(directory)
            with self.assertRaises(ReachedFactory):
                fixture.workspace()
            self.assertEqual(calls, [(fixture, fixture.root / 'execution', None)])

    def test_geometry_workspace_reaches_configured_broker_factory(self):
        self.check_factory(geometry_fixture.WorkspaceGeometryNativeCrossHostTests)

    def test_cabinet_workspace_reaches_configured_broker_factory(self):
        self.check_factory(cabinet_fixture.CabinetNativeWitnessTests)


if __name__ == '__main__':
    unittest.main(verbosity=2)
