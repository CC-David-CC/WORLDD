#!/usr/bin/env python3
"""Test that a live production-shaped app mount cannot see later broker staging."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROCESS_CPP = ROOT / "src/workspace/process.cpp"

class CopyStagingNamespaceTests(unittest.TestCase):
    def test_already_running_namespace_hides_staging_created_during_copy(self):
        source = PROCESS_CPP.read_text()
        project_bind = source.index('bind, "3", "/project"')
        stage_mask = source.index('"--tmpfs", "/project/.spatial-copy-staging"')
        mask_ro = source.index('"--remount-ro", "/project/.spatial-copy-staging"')
        project_alias = source.index('"--symlink", "/project", project.string()')
        self.assertLess(project_bind, stage_mask)
        self.assertLess(stage_mask, mask_ro)
        self.assertLess(mask_ro, project_alias)
        self.assertIn('"--remount-ro", "/"', source)

        # Production uses this AppArmor-authorized entry point for user/net namespaces.
        bwrap = Path("/usr/local/libexec/spatial-bwrap")
        self.assertTrue(bwrap.is_file() and os.access(bwrap, os.X_OK),
                        "production spatial-bwrap launcher is unavailable")
        payload = b"broker-verified-payload-that-must-not-be-tampered"
        with tempfile.TemporaryDirectory(prefix="spatial-copy-live-stage-") as temporary:
            project = Path(temporary) / "project"
            stage_root = project / ".spatial-copy-staging"
            job = stage_root / ".job-live-adversary"
            project.mkdir()
            # Production creates and validates this mountpoint before app launch.
            stage_root.mkdir(mode=0o700)
            os.chmod(stage_root, 0o700)

            child = r"""
import errno, json, os, sys
stage = "/project/.spatial-copy-staging"
target = stage + "/.job-live-adversary/output"
def probe(label):
    result = {"phase": label, "entries": os.listdir(stage), "read_denied": False,
              "write_denied": False, "create_denied": False}
    try:
        with open(target, "rb") as stream: result["read"] = stream.read().decode()
    except OSError as error:
        result["read_denied"] = error.errno in (errno.ENOENT, errno.EACCES, errno.EROFS)
    try:
        with open(target, "wb") as stream: stream.write(b"attacker")
    except OSError as error:
        result["write_denied"] = error.errno in (errno.ENOENT, errno.EACCES, errno.EROFS)
    try:
        os.mkdir(stage + "/attacker-entry")
    except OSError as error:
        result["create_denied"] = error.errno in (errno.EACCES, errno.EROFS)
    with open("/project/app-stays-writable", "wb") as stream: stream.write(b"yes")
    print(json.dumps(result), flush=True)
for line in sys.stdin:
    if line.strip() == "probe": probe("probe")
    elif line.strip() == "quit": break
"""
            command = [
                bwrap, "--unshare-all", "--unshare-user", "--disable-userns",
                "--die-with-parent", "--new-session", "--cap-drop", "ALL",
                "--hostname", "spatial-workspace", "--clearenv",
                "--setenv", "PATH", "/usr/bin:/bin",
                "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib",
                "--ro-bind", "/usr/share", "/usr/share",
                "--ro-bind", "/usr/lib64", "/usr/lib64",
                "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
                "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                "--bind", str(project), "/project",
                "--tmpfs", "/project/.spatial-copy-staging",
                "--remount-ro", "/project/.spatial-copy-staging",
                "--symlink", "/project", str(project), "--remount-ro", "/",
                "--chdir", "/project", "--", "/usr/bin/python3", "-B", "-c", child,
            ]
            app = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, bufsize=1)
            try:
                first = self._probe(app)
                self.assertEqual(first["phase"], "probe")
                self.assertEqual(first["entries"], [])
                self.assertTrue(first["read_denied"], first)
                self.assertTrue(first["write_denied"], first)
                self.assertTrue(first["create_denied"], first)

                # Simulate the broker creating a job only after the app namespace is live.
                job.mkdir(mode=0o700)
                staged = job / "output"
                staged.write_bytes(payload)
                staged.chmod(0o600)
                before = hashlib.sha256(staged.read_bytes()).digest()
                during = self._probe(app)
                self.assertEqual(during["entries"], [])
                self.assertTrue(during["read_denied"], during)
                self.assertTrue(during["write_denied"], during)
                self.assertTrue(during["create_denied"], during)
                self.assertEqual(hashlib.sha256(staged.read_bytes()).digest(), before)
                self.assertEqual(staged.read_bytes(), payload)
                self.assertEqual((project / "app-stays-writable").read_bytes(), b"yes")

                # The broker publishes only the verified pinned output with no replacement.
                os.rename(staged, project / "published")
                self.assertEqual((project / "published").read_bytes(), payload)
                self.assertEqual(hashlib.sha256((project / "published").read_bytes()).digest(), before)
            finally:
                try:
                    app.stdin.write("quit\n")
                    app.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                try:
                    _, stderr = app.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    app.kill()
                    _, stderr = app.communicate()
                    self.fail("long-lived app namespace did not exit")
                self.assertEqual(app.returncode, 0, stderr)

    @staticmethod
    def _probe(app):
        app.stdin.write("probe\n")
        app.stdin.flush()
        line = app.stdout.readline()
        if not line:
            stderr = app.stderr.read()
            raise AssertionError(f"app namespace exited before probe: {stderr}")
        return json.loads(line)

if __name__ == "__main__":
    unittest.main()
