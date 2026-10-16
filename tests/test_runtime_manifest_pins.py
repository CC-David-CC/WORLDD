import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "check_runtime_manifest_pins.py"
SPEC = importlib.util.spec_from_file_location("check_runtime_manifest_pins", TOOL)
CHECKER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CHECKER)
GOOD_SHA = "a" * 64
OTHER_SHA = "b" * 64

def entry(path, digest=GOOD_SHA):
    return {"path": path, "sha256": digest}

def manifest():
    hosts = {}
    for host in CHECKER.HOSTS:
        hosts[host] = {}
        for profile in CHECKER.PROFILES:
            hosts[host][profile] = {
                "server_receipt": entry(f"/srv/{host}/{profile}/server.json"),
                "xace_wrapper": entry(f"/srv/{host}/{profile}/Xvfb"),
                "egl_files": [
                    entry("/opt/egl/vendor.json"),
                    entry("/opt/egl/libEGL_mesa.so.0.0.0"),
                    entry("/usr/local/libexec/spatial-storage-owner"),
                ],
                "installed_helpers": [
                    entry("/usr/local/libexec/spatial-bwrap"),
                    entry("/usr/local/libexec/spatial-storage-owner"),
                ],
            }
    return {
        "hosts": hosts,
        "required_egl_names": ["vendor.json", "libEGL_mesa.so.0.0.0"],
        "required_helper_names": ["spatial-bwrap", "spatial-storage-owner"],
    }

class RuntimeManifestPinTests(unittest.TestCase):
    def test_same_path_same_sha_is_allowed(self):
        self.assertEqual(CHECKER.validate_manifest(manifest()), [])

    def test_same_path_conflicting_sha_reports_host_profile_and_categories(self):
        data = manifest()
        data["hosts"]["sb16"]["Debug"]["egl_files"][-1]["sha256"] = OTHER_SHA
        conflicts = CHECKER.validate_manifest(data)
        self.assertEqual(len(conflicts), 1)
        for expected in (
            "sb16/Debug", "/usr/local/libexec/spatial-storage-owner",
            "egl_files=" + OTHER_SHA, "installed_helpers=" + GOOD_SHA,
        ):
            self.assertIn(expected, conflicts[0])

    def test_missing_required_helper_is_rejected_structurally(self):
        data = manifest()
        data["hosts"]["sb16"]["Debug"]["installed_helpers"].pop()
        with self.assertRaisesRegex(CHECKER.ManifestError, "required helpers missing"):
            CHECKER.validate_manifest(data)

    def test_cli_accepts_consistent_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps(manifest()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL), str(path)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("OK:", result.stdout)

    def test_cli_returns_nonzero_for_conflict_without_rewriting_input(self):
        data = manifest()
        data["hosts"]["sb16"]["Debug"]["egl_files"][-1]["sha256"] = OTHER_SHA
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            original = json.dumps(data, sort_keys=True).encode()
            path.write_bytes(original)
            result = subprocess.run(
                [sys.executable, str(TOOL), str(path)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("CONFLICT:", result.stderr)
            self.assertEqual(path.read_bytes(), original)

if __name__ == "__main__":
    unittest.main()
