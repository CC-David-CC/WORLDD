"""External storage evidence roots stay private and compatible with native fixtures."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve()
GATE_PATH = HERE.parents[2] / "tools" / "m06_deployed_gate.py"
HELPER_PATH = HERE.parent / "storage_session_paths.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load_module("m06_storage_evidence_gate", GATE_PATH)
storage_paths = load_module("native_storage_session_paths", HELPER_PATH)


class ExternalStorageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifacts = self.root / "spatial-artifacts"
        self.artifacts.mkdir(mode=0o700)
        self.original_root = gate.STORAGE_EVIDENCE_ROOT
        self.original_helper_root = storage_paths.ARTIFACT_ROOT
        gate.STORAGE_EVIDENCE_ROOT = self.artifacts / "m06-gate-storage"
        storage_paths.ARTIFACT_ROOT = self.artifacts
        self.source = self.root / "frozen-source"
        self.source.mkdir(mode=0o700)
        self.sentinel = self.source / "sentinel"
        self.sentinel.write_bytes(b"frozen source")
        self.source.chmod(0o500)
        self.snapshot = self._snapshot_source()

    def tearDown(self):
        gate.STORAGE_EVIDENCE_ROOT = self.original_root
        storage_paths.ARTIFACT_ROOT = self.original_helper_root
        self.source.chmod(0o700)
        self.temp.cleanup()

    def _snapshot_source(self):
        return [(str(path.relative_to(self.source)), path.stat().st_mode & 0o777,
                 path.read_bytes() if path.is_file() else None)
                for path in [self.source, *sorted(self.source.rglob("*"))]]

    def reserve(self, host="sb16", attempt="attempt-01"):
        return gate.create_storage_evidence_attempt(self.source, host, attempt)

    def test_both_profiles_create_external_roots_and_native_helper_accepts_paths(self):
        for host in ("sb16", "sb16-2"):
            attempt = self.reserve(host, "valid-" + host)
            self.assertNotIn(self.source, attempt["root"].resolve().parents)
            for profile in gate.PROFILES:
                row = gate.create_storage_evidence_profile(attempt["root"], self.source, profile)
                self.assertTrue(row["path"].endswith("/" + profile))
                self.assertEqual(row["mode"], 0o700)
                for test_name, registration in row["registrations"].items():
                    evidence_root = Path(registration["path"])
                    self.assertEqual(evidence_root.name, "spatial_security_evidence")
                    method_root = evidence_root / "test_native_storage_metadata"
                    method_root.mkdir(mode=0o700)
                    accepted = storage_paths.validate_fixture_root(
                        host, evidence_root, method_root, os.getuid())
                    self.assertEqual(accepted, method_root)
                    self.assertEqual(gate.verify_storage_evidence_path(self.source, evidence_root)[-1]["mode"], 0o700)
                    self.assertIn(test_name, gate.STORAGE_LOCAL_EVIDENCE_TESTS)
        self.assertEqual(self._snapshot_source(), self.snapshot)
        self.assertFalse((self.source / "local").exists())

    def test_invalid_host_is_rejected_before_external_creation(self):
        with self.assertRaisesRegex(RuntimeError, "approved test host"):
            self.reserve("a5500", "bad-host")
        self.assertFalse(gate.STORAGE_EVIDENCE_ROOT.exists())

    def test_invalid_profile_is_rejected_before_profile_creation(self):
        attempt = self.reserve()
        with self.assertRaisesRegex(RuntimeError, "profile is not approved"):
            gate.create_storage_evidence_profile(attempt["root"], self.source, "../Debug")
        self.assertEqual(list(attempt["root"].iterdir()), [])

    def test_traversal_attempt_identifier_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "identifier is invalid"):
            self.reserve("sb16", "../outside")
        self.assertFalse(gate.STORAGE_EVIDENCE_ROOT.exists())

    def test_symlinked_artifact_ancestor_is_rejected_before_creation(self):
        actual = self.root / "actual-parent"
        actual.mkdir(mode=0o700)
        link = self.root / "linked-parent"
        link.symlink_to(actual, target_is_directory=True)
        gate.STORAGE_EVIDENCE_ROOT = link / "m06-gate-storage"
        with self.assertRaisesRegex(RuntimeError, "real directory|symlinked parent"):
            self.reserve("sb16", "symlink-parent")
        self.assertFalse((actual / "m06-gate-storage").exists())

    def test_symlinked_host_parent_is_rejected(self):
        gate.STORAGE_EVIDENCE_ROOT.mkdir(mode=0o700)
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (gate.STORAGE_EVIDENCE_ROOT / "sb16").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "real directory"):
            self.reserve()

    def test_outside_and_noncanonical_paths_are_rejected(self):
        attempt = self.reserve()
        for path in (self.root / "outside", gate.STORAGE_EVIDENCE_ROOT / "sb16" / "bad" / "Debug"):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                gate.verify_storage_evidence_path(self.source, path)

    def test_wrong_component_owner_is_rejected(self):
        attempt = self.reserve()
        with patch.object(gate.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(RuntimeError, "unexpected owner"):
                gate.verify_storage_evidence_path(self.source, attempt["root"])

    def test_changed_leaf_mode_is_rejected(self):
        attempt = self.reserve()
        profile = gate.create_storage_evidence_profile(attempt["root"], self.source, "Debug")
        leaf = Path(profile["registrations"]["session-storage-owner"]["path"])
        leaf.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, "mode"):
            gate.verify_storage_evidence_path(self.source, leaf)

    def test_duplicate_attempt_is_refused(self):
        self.reserve()
        with self.assertRaises(FileExistsError):
            self.reserve()

    def test_symlinked_registration_parent_is_rejected(self):
        attempt = self.reserve()
        profile = gate.create_storage_evidence_profile(attempt["root"], self.source, "Debug")
        evidence = Path(profile["registrations"]["session-storage-owner"]["path"])
        parent = evidence.parent
        evidence.rmdir()
        evidence.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "real directory"):
            gate.verify_storage_evidence_path(self.source, evidence)
        self.assertEqual(parent.name, "session-storage-owner")

    def test_only_two_storage_registration_roots_are_created(self):
        attempt = self.reserve()
        profile = gate.create_storage_evidence_profile(attempt["root"], self.source, "ASan-UBSan")
        self.assertEqual(set(profile["registrations"]), set(gate.STORAGE_LOCAL_EVIDENCE_TESTS))
        self.assertEqual(len(list(Path(profile["path"]).iterdir())), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
