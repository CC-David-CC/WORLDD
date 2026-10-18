#!/usr/bin/env python3
"""Focused path and permissions checks for private test evidence setup."""
from pathlib import Path
import tempfile
import unittest

from with_private_test_evidence import create_private_evidence, resolve_root


class PrivateEvidenceSetupTests(unittest.TestCase):
    def test_gateway_registration_uses_runtime_evidence_wrapper(self):
        cmake = Path(__file__).resolve().parents[1] / 'CMakeLists.txt'
        source = cmake.read_text()
        start = source.index('add_test(NAME workspace-actor-cabinet-gateway COMMAND')
        end = source.index('set_tests_properties(workspace-actor-cabinet-gateway', start)
        registration = source[start:end]
        self.assertIn('tools/with_private_test_evidence.py', registration)
        self.assertNotIn('CMAKE_CURRENT_BINARY_DIR', registration)
        self.assertNotIn('actor-cabinet-private-evidence', registration)

    def test_explicit_environment_root_is_used(self):
        root = resolve_root(environ={'SPATIAL_SECURITY_EVIDENCE': '/tmp/explicit-evidence'})
        self.assertEqual(root, Path('/tmp/explicit-evidence'))

    def test_missing_environment_uses_external_artifact_default(self):
        root = resolve_root(environ={}, home=Path('/home/test-user'))
        self.assertEqual(root, Path('/home/test-user/spatial-artifacts/security-evidence/actor-cabinet-gateway'))

    def test_private_leaf_is_created_outside_frozen_package(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = base / 'bundle'
            (package / 'source' / 'tools').mkdir(parents=True)
            (package / 'PACKAGE_MANIFEST.SHA256SUMS').write_text('manifest')
            evidence_root = base / 'external-evidence'
            leaf = create_private_evidence(evidence_root, script=package / 'source/tools/wrapper.py', cwd=package)
            self.assertTrue(leaf.is_relative_to(evidence_root))
            self.assertEqual(leaf.stat().st_mode & 0o777, 0o700)

    def test_frozen_bundle_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / 'bundle'
            (package / 'builds' / 'debug').mkdir(parents=True)
            (package / 'PACKAGE_MANIFEST.SHA256SUMS').write_text('manifest')
            with self.assertRaisesRegex(RuntimeError, 'inside frozen package'):
                create_private_evidence(package / 'builds/debug/evidence',
                                        script=package / 'source/tools/wrapper.py', cwd=package)


if __name__ == '__main__':
    unittest.main()
