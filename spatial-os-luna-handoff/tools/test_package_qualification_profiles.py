#!/usr/bin/env python3
import ast
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = ROOT / "tools/package_debug_qualification.sh"
CMAKE_FILE = ROOT / "CMakeLists.txt"


def input_tests(cmake_text):
    lines = cmake_text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == "if(SPATIAL_INPUT_TESTS)"]
    if len(starts) != 1:
        raise AssertionError(f"expected one SPATIAL_INPUT_TESTS block, found {len(starts)}")
    depth = 0
    end = None
    for i in range(starts[0], len(lines)):
        line = lines[i].strip()
        if line.startswith("if("):
            depth += 1
        if line == "endif()":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        raise AssertionError("SPATIAL_INPUT_TESTS block is not closed")
    block = "\n".join(lines[starts[0]:end + 1])
    names = re.findall(r"add_test\(\s*NAME\s+([^\s)]+)", block)
    identity_suites = re.search(r"foreach\(identity_suite\s+([^)]*)\)", block)
    marker = chr(36) + "{identity_suite}"
    expanded = set()
    for name in names:
        if marker in name:
            if not identity_suites:
                raise AssertionError("identity test registration has no foreach")
            expanded.update(name.replace(marker, suite)
                           for suite in identity_suites.group(1).split())
        else:
            if chr(36) in name:
                raise AssertionError(f"unexpanded test name: {name}")
            expanded.add(name)
    return expanded


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
        self.assertIn("missing_input=sorted(set(expanded_input_names)-set(names))", self.script)
        self.assertIn("SPATIAL_INPUT_TESTS registrations missing from package", self.script)


if __name__ == "__main__":
    unittest.main()
