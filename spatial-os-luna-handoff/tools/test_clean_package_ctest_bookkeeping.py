#!/usr/bin/env python3
"""Behavioral regression tests for package CTest bookkeeping cleanup."""

import os
import pathlib
import subprocess
import tempfile
import unittest


HELPER = pathlib.Path(__file__).with_name("clean_package_ctest_bookkeeping.py")


class PackageCTestGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="package-ctest-guard-")
        self.final = pathlib.Path(self.temp.name) / "package"
        self.build = self.final / "builds" / "debug"
        self.build.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def run_guard(self):
        return subprocess.run(
            ["python3", str(HELPER), str(self.final), str(self.build)],
            text=True, capture_output=True, check=False,
        )

    def test_removes_only_empty_nested_ctest_directories(self):
        nested = self.build / "Testing" / "Temporary" / "empty"
        nested.mkdir(parents=True)
        (nested / "Testing" / "nested-empty").mkdir(parents=True)
        (self.build / "Testing" / "Measurements").mkdir()
        (self.build / "Testing.txt").mkdir()
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.build / "Testing").exists())
        self.assertTrue((self.build / "Testing.txt").is_dir())

    def test_rejects_regular_ctest_log_without_deleting_anything(self):
        (self.build / "Testing" / "Temporary").mkdir(parents=True)
        log = self.build / "Testing" / "Temporary" / "LastTest.log"
        log.write_text("retained evidence\n")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-directory", result.stderr)
        self.assertEqual(log.read_text(), "retained evidence\n")
        self.assertTrue((self.build / "Testing" / "Temporary").is_dir())

    def test_rejects_testing_symlink_without_following_it(self):
        target = self.final / "outside"
        target.mkdir()
        (target / "sentinel").write_text("untouched")
        os.symlink(target, self.build / "Testing")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("real directory", result.stderr)
        self.assertTrue((target / "sentinel").is_file())
        self.assertTrue((self.build / "Testing").is_symlink())

    def test_preserves_unrelated_testing_txt(self):
        marker = self.build / "Testing.txt"
        marker.write_text("unrelated")
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), "unrelated")

    def test_rejects_testing_path_outside_build_before_cleanup(self):
        (self.build / "Testing" / "Temporary").mkdir(parents=True)
        outside = self.final / "source" / "Testing"
        outside.mkdir(parents=True)
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside BUILD/Testing", result.stderr)
        self.assertTrue((self.build / "Testing" / "Temporary").is_dir())
        self.assertTrue(outside.is_dir())

    def test_validates_build_root_before_cleanup(self):
        (self.build / "Testing" / "Temporary").mkdir(parents=True)
        real_build = self.final / "builds" / "debug-real"
        self.build.rename(real_build)
        self.build.symlink_to(real_build, target_is_directory=True)
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root must be a real directory", result.stderr)
        self.assertTrue((real_build / "Testing" / "Temporary").is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
