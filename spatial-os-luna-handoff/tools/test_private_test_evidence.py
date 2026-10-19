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

from with_private_test_evidence import create_private_evidence, create_private_evidence_children, resolve_root


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


class PrivateEvidenceParentsTests(unittest.TestCase):
    def make_parent(self, path: Path) -> Path:
        path.mkdir(mode=0o700)
        path.chmod(0o700)
        return path

    def test_fresh_case_and_scope_roots_stay_private_under_umasks_022_and_077(self):
        for mask in (0o022, 0o077):
            previous = os.umask(mask)
            try:
                with tempfile.TemporaryDirectory() as temp:
                    parent = self.make_parent(Path(temp) / 'owned-evidence')
                    unrelated = parent / 'unrelated'
                    unrelated.mkdir(mode=0o700)
                    cases, scopes = create_private_evidence_children(parent)
                    self.assertEqual((cases, scopes), (parent / 'cases', parent / 'scopes'))
                    for child in (cases, scopes):
                        info = child.lstat()
                        self.assertTrue(stat.S_ISDIR(info.st_mode))
                        self.assertEqual(info.st_uid, os.getuid())
                        self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
                    self.assertTrue(unrelated.is_dir())
                    self.assertEqual(stat.S_IMODE(unrelated.stat().st_mode), 0o700)
            finally:
                os.umask(previous)

    def test_existing_child_refuses_before_mutating_sibling_or_existing_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = self.make_parent(Path(temp) / 'owned-evidence')
            scopes = parent / 'scopes'
            scopes.mkdir(mode=0o711)
            scopes.chmod(0o711)
            with self.assertRaisesRegex(FileExistsError, 'child already exists'):
                create_private_evidence_children(parent)
            self.assertFalse((parent / 'cases').exists())
            self.assertEqual(stat.S_IMODE(scopes.stat().st_mode), 0o711)

    def test_symlink_child_refuses_without_following_or_mutating_target(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            parent = self.make_parent(base / 'owned-evidence')
            target = base / 'target'
            target.mkdir(mode=0o700)
            (parent / 'scopes').symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(FileExistsError, 'child already exists'):
                create_private_evidence_children(parent)
            self.assertFalse((parent / 'cases').exists())
            self.assertTrue((parent / 'scopes').is_symlink())
            self.assertEqual(list(target.iterdir()), [])

    def test_public_parent_is_rejected_without_chmod_or_children(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / 'owned-evidence'
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, 'owned mode-0700'):
                create_private_evidence_children(parent)
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
            self.assertFalse((parent / 'cases').exists())
            self.assertFalse((parent / 'scopes').exists())

    def test_symlink_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            parent = self.make_parent(base / 'owned-evidence')
            alias = base / 'alias'
            alias.symlink_to(parent, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'owned mode-0700'):
                create_private_evidence_children(alias)
            self.assertFalse((parent / 'cases').exists())
            self.assertFalse((parent / 'scopes').exists())



if __name__ == '__main__':
    unittest.main()
