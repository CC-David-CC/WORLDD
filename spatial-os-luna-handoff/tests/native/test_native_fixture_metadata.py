"""Metadata-only regressions for native process and storage fixture helpers."""
import os
from pathlib import Path
import tempfile
import unittest

from native_process_argv import argv0_matches, parse_proc_cmdline
from storage_session_paths import ARTIFACT_ROOT, validate_fixture_root


class NativeFixtureMetadataTests(unittest.TestCase):
    def test_proc_argv_preserves_args_and_classifies_exact_executable(self):
        source = (
            b"/usr/bin/python3\0-c\0program mentions /project/probe\0"
            b"--label=/project/probe-other\0\0"
        )
        argv = parse_proc_cmdline(source)
        self.assertEqual(argv[0], "/usr/bin/python3")
        self.assertIn("program mentions /project/probe", argv)
        self.assertIn("--label=/project/probe-other", argv)
        self.assertEqual(argv[-1], "")
        self.assertTrue(argv0_matches(argv, "/usr/bin/python3"))
        self.assertFalse(argv0_matches(argv, "/project/probe"))
        probe = parse_proc_cmdline(b"/project/probe\0--id\0coordinator-application\0")
        self.assertTrue(argv0_matches(probe, "/project/probe"))
        self.assertFalse(argv0_matches(probe, "/usr/bin/python3"))

    def _fixture_root(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-fixture-control-", dir=ARTIFACT_ROOT)
        evidence = Path(temporary.name) / "spatial_security_evidence"
        evidence.mkdir(mode=0o755)
        root = evidence / "test_native_storage_metadata_control"
        root.mkdir(mode=0o700)
        return temporary, evidence, root

    def test_valid_external_method_root_is_accepted(self):
        temporary, evidence, root = self._fixture_root()
        try:
            self.assertEqual(
                validate_fixture_root("sb16", evidence, root, os.getuid()),
                root.resolve(strict=True),
            )
        finally:
            temporary.cleanup()

    def test_all_registered_storage_session_method_roots_are_accepted(self):
        import ast
        tree = ast.parse(Path(__file__).with_name('test_storage_session.py').read_text())
        suite = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                     and node.name == 'NativeStorageSessionTests')
        methods = [node.name for node in suite.body if isinstance(node, ast.FunctionDef)
                   and node.name.startswith('test_')]
        self.assertEqual(len(methods), 7)
        temporary, evidence, root = self._fixture_root()
        try:
            for method in methods:
                current = evidence / method
                current.mkdir(mode=0o700)
                self.assertEqual(validate_fixture_root('sb16', evidence, current, os.getuid()), current)
        finally:
            temporary.cleanup()

    def test_unregistered_cleanup_method_root_is_rejected(self):
        temporary, evidence, root = self._fixture_root()
        try:
            wrong = evidence / 'test_cleanup_unregistered_method'
            root.rename(wrong)
            with self.assertRaisesRegex(ValueError, 'test method name'):
                validate_fixture_root('sb16', evidence, wrong, os.getuid())
        finally:
            temporary.cleanup()

    def test_evidence_root_outside_artifact_namespace_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="native-fixture-escape-") as directory:
            evidence = Path(directory) / "spatial_security_evidence"
            evidence.mkdir()
            root = evidence / "test_native_storage_escape"
            root.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError, "spatial-artifacts"):
                validate_fixture_root("sb16", evidence, root, os.getuid())

    def test_wrong_host_is_rejected(self):
        temporary, evidence, root = self._fixture_root()
        try:
            with self.assertRaisesRegex(ValueError, "not permitted on host"):
                validate_fixture_root("a5500", evidence, root, os.getuid())
        finally:
            temporary.cleanup()

    def test_wrong_owner_is_rejected(self):
        temporary, evidence, root = self._fixture_root()
        try:
            with self.assertRaisesRegex(ValueError, "fixture root must be owned by the operator"):
                validate_fixture_root(
                    "sb16", evidence, root, os.getuid() + 1,
                    evidence_owner_uid=os.getuid(),
                )
        finally:
            temporary.cleanup()

    def test_wrong_evidence_owner_is_rejected(self):
        temporary, evidence, root = self._fixture_root()
        try:
            with self.assertRaisesRegex(ValueError, "evidence root must be owned by the operator"):
                validate_fixture_root(
                    "sb16", evidence, root, os.getuid(),
                    evidence_owner_uid=os.getuid() + 1,
                )
        finally:
            temporary.cleanup()

    def test_method_root_must_remain_private(self):
        temporary, evidence, root = self._fixture_root()
        try:
            root.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "must be private"):
                validate_fixture_root("sb16", evidence, root, os.getuid())
        finally:
            temporary.cleanup()

    def test_symlink_evidence_parent_is_rejected(self):
        temporary, evidence, root = self._fixture_root()
        try:
            real_evidence = evidence.parent / "real-evidence"
            evidence.rename(real_evidence)
            evidence.symlink_to(real_evidence, target_is_directory=True)
            linked_root = evidence / root.name
            with self.assertRaisesRegex(ValueError, "contains symlink"):
                validate_fixture_root("sb16", evidence, linked_root, os.getuid())
        finally:
            temporary.cleanup()

    def test_symlink_method_root_is_rejected(self):
        temporary, evidence, root = self._fixture_root()
        try:
            link = evidence / "test_native_storage_link"
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "contains symlink"):
                validate_fixture_root("sb16", evidence, link, os.getuid())
        finally:
            temporary.cleanup()

    def test_non_method_directory_name_is_rejected(self):
        temporary, evidence, root = self._fixture_root()
        try:
            other = evidence / "unrelated-data"
            root.rename(other)
            with self.assertRaisesRegex(ValueError, "test method name"):
                validate_fixture_root("sb16", evidence, other, os.getuid())
        finally:
            temporary.cleanup()

    def test_dotdot_fixture_path_is_rejected(self):
        temporary, evidence, root = self._fixture_root()
        try:
            via_parent = evidence / "intermediate" / ".." / root.name
            with self.assertRaisesRegex(ValueError, "absolute normalized path"):
                validate_fixture_root("sb16", evidence, via_parent, os.getuid())
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
