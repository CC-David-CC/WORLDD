#!/usr/bin/env python3
import ast
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from package_qualification_inventory import (
    input_tests, registration_names_from_text, validate_inventory,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = ROOT / "tools/package_debug_qualification.sh"
CMAKE_FILE = ROOT / "CMakeLists.txt"



class PackageQualificationProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = PACKAGE_SCRIPT.read_text()
        cls.cmake = CMAKE_FILE.read_text()

    def test_profiles_select_build_and_sanitizer_modes(self):
        profile_case = re.search(r'case "\$PROFILE" in(.*?)esac', self.script, re.S)
        self.assertIsNotNone(profile_case)
        cases = profile_case.group(1)
        self.assertRegex(cases, r"Debug\)\s+BUILD_DIR_NAME=debug\s+SANITIZERS=OFF")
        self.assertRegex(cases, r"ASan-UBSan\)\s+BUILD_DIR_NAME=asan-ubsan\s+SANITIZERS=ON")
        self.assertIn('BUILD="$FINAL/builds/$BUILD_DIR_NAME"', self.script)
        self.assertIn("build_profile=%s", self.script)
        self.assertIn("spatial_sanitizers=%s", self.script)

    def test_missing_and_invalid_profiles_reject_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            outputs = [root / "package", root / "package.tar.gz", root / "ctest"]
            missing = subprocess.run(
                ["bash", str(PACKAGE_SCRIPT)], text=True, capture_output=True, check=False)
            self.assertEqual(missing.returncode, 64)
            self.assertIn("usage:", missing.stderr)
            invalid = subprocess.run(
                ["bash", str(PACKAGE_SCRIPT), str(root / "missing-source"), "Invalid",
                 *(str(path) for path in outputs)],
                text=True, capture_output=True, check=False)
            self.assertEqual(invalid.returncode, 64)
            self.assertIn("unsupported profile", invalid.stderr)
            self.assertFalse(any(path.exists() for path in outputs))

    def test_both_profiles_use_required_ninja_configuration(self):
        collapsed = self.script.replace(chr(92) + chr(10), " ")
        configure = re.search(r'cmake -G Ninja .*?SPATIAL_SANITIZERS="\$SANITIZERS"', collapsed)
        self.assertIsNotNone(configure)
        command = configure.group(0)
        for required in (
            "-DCMAKE_BUILD_TYPE=Debug",
            "-DBUILD_TESTING=ON",
            "-DSPATIAL_INPUT_TESTS=ON",
        ):
            self.assertIn(required, command)
        self.assertNotIn("SPATIAL_INPUT_TESTS=OFF", self.script)

    def test_embedded_python_helpers_are_syntactically_valid(self):
        blocks = [chunk.split(chr(10) + "PY", 1)[0]
                  for chunk in self.script.split("<<'PY'" + chr(10))[1:]]
        self.assertEqual(len(blocks), 4)
        for block in blocks:
            ast.parse(block)

    def test_every_input_test_must_appear_in_ctest_inventory(self):
        registered = input_tests(self.cmake)
        self.assertGreaterEqual(len(registered), 35)
        self.assertTrue({
            "broker-control-client-live",
            "workspace-participant-native",
            "application-control-lease",
            "actor-pixels",
            "native-actor-input",
        }.issubset(registered))
        self.assertIn("validate_inventory(registered, j, cmake, build)", self.script)
        self.assertIn("registered_count=$(wc -l <", self.script)
        self.assertIn("registration_names_from_text(text, str(src))", self.script)

    def test_dynamic_inventory_matches_copied_metadata_and_json(self):
        identity_name = chr(36) + "{identity_suite}"
        cmake = (
            "if(SPATIAL_INPUT_TESTS)\n"
            "foreach(identity_suite basic tls)\n"
            " add_test(NAME identity-" + identity_name + " COMMAND identity-test)\n"
            " add_test(NAME physical-input COMMAND input-test)\n"
            "endif()\n"
        )
        registrations = [
            "identity-basic", "identity-tls", "physical-input", "world-smoke",
        ]
        files = [
            "add_test([=[identity-basic]=] identity-basic)\n"
            "add_test([=[physical-input]=] physical-input)\n",
            "add_test([=[identity-tls]=] identity-tls)\n"
            "add_test([=[world-smoke]=] world-smoke)\n",
        ]
        copied = []
        for index, content in enumerate(files):
            copied.extend(registration_names_from_text(
                content, f"CTestTestfile-{index}.cmake"))
        ctest_json = {"tests": [
            {"name": name, "properties": [
                {"name": "WORKING_DIRECTORY", "value": "/pkg/build"},
            ]}
            for name in registrations
        ]}
        result = validate_inventory(
            copied, ctest_json, cmake, pathlib.Path("/pkg/build"))
        self.assertEqual(result, {
            "registered_count": 4,
            "input_count": 3,
            "working_directory_count": 4,
        })

    def test_inventory_rejects_registration_json_input_and_workdir_mismatches(self):
        cmake = (
            "if(SPATIAL_INPUT_TESTS)\n"
            " add_test(NAME required-input COMMAND input-test)\n"
            "endif()\n"
        )
        valid = ["required-input", "other-test"]
        good_json = {"tests": [
            {"name": name, "properties": [
                {"name": "WORKING_DIRECTORY", "value": "/pkg/build"},
            ]}
            for name in valid
        ]}
        with self.assertRaisesRegex(AssertionError, "differs from copied"):
            validate_inventory(valid[:1], good_json, cmake, pathlib.Path("/pkg/build"))
        missing_input_json = {"tests": [
            {"name": "other-test", "properties": [
                {"name": "WORKING_DIRECTORY", "value": "/pkg/build"},
            ]}
        ]}
        with self.assertRaisesRegex(AssertionError, "missing from package"):
            validate_inventory(
                ["other-test"], missing_input_json, cmake, pathlib.Path("/pkg/build"))
        bad_workdir = {"tests": [
            {"name": name, "properties": [
                {"name": "WORKING_DIRECTORY", "value": "/wrong"},
            ]}
            for name in valid
        ]}
        with self.assertRaisesRegex(AssertionError, "working dirs"):
            validate_inventory(valid, bad_workdir, cmake, pathlib.Path("/pkg/build"))
        with self.assertRaisesRegex(AssertionError, "duplicate"):
            validate_inventory(valid + [valid[0]], good_json, cmake, pathlib.Path("/pkg/build"))

    def test_registration_parser_rejects_duplicate_names_per_file(self):
        metadata = (
            "add_test([=[same]=] one)\n"
            "add_test([=[same]=] two)\n"
        )
        with self.assertRaisesRegex(AssertionError, "duplicate"):
            registration_names_from_text(metadata, "duplicate.CTestTestfile.cmake")


if __name__ == "__main__":
    unittest.main()
