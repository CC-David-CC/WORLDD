#!/usr/bin/env python3
"""Focused path and permissions checks for private test evidence setup."""
from pathlib import Path
import os
import shutil
import stat
import subprocess
import sys
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
            (package / 'PACKAGE_MANIFEST.sha256').write_text('manifest')
            evidence_root = base / 'external-evidence'
            leaf = create_private_evidence(evidence_root, script=package / 'source/tools/wrapper.py', cwd=package)
            self.assertTrue(leaf.is_relative_to(evidence_root))
            self.assertEqual(stat.S_IMODE(leaf.stat().st_mode), 0o700)

    def test_current_frozen_bundle_marker_rejects_inside_root(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / 'bundle'
            (package / 'builds' / 'debug').mkdir(parents=True)
            (package / 'PACKAGE_MANIFEST.sha256').write_text('manifest')
            with self.assertRaisesRegex(RuntimeError, 'inside frozen package'):
                create_private_evidence(package / 'builds/debug/evidence',
                                        script=package / 'source/tools/wrapper.py', cwd=package)

    def test_legacy_frozen_bundle_marker_rejects_inside_root(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / 'bundle'
            (package / 'builds' / 'debug').mkdir(parents=True)
            (package / 'PACKAGE_MANIFEST.SHA256SUMS').write_text('legacy manifest')
            with self.assertRaisesRegex(RuntimeError, 'inside frozen package'):
                create_private_evidence(package / 'builds/debug/evidence',
                                        script=package / 'source/tools/wrapper.py', cwd=package)

    def test_wrapper_passes_private_external_root_to_child(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            package = base / 'bundle'
            tools = package / 'source' / 'tools'
            tools.mkdir(parents=True)
            (package / 'PACKAGE_MANIFEST.sha256').write_text('manifest')
            wrapper = tools / 'with_private_test_evidence.py'
            shutil.copy2(Path(__file__).with_name('with_private_test_evidence.py'), wrapper)
            evidence_root = base / 'external-evidence'
            child = (
                'import os, stat; from pathlib import Path; '
                'p = Path(os.environ["SPATIAL_SECURITY_EVIDENCE"]); '
                'print(p); '
                'assert p.is_relative_to(Path(' + repr(str(evidence_root)) + ')); '
                'assert stat.S_IMODE(p.stat().st_mode) == 0o700'
            )
            result = subprocess.run(
                [sys.executable, str(wrapper), '--root', str(evidence_root), '--', sys.executable, '-c', child],
                cwd=package, text=True, capture_output=True, check=False,
                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            passed_path = Path(result.stdout.strip())
            self.assertTrue(passed_path.is_relative_to(evidence_root.resolve()))
            self.assertEqual(stat.S_IMODE(passed_path.stat().st_mode), 0o700)


if __name__ == '__main__':
    unittest.main()
