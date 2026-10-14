"""Confined immutable tree-copy helper regressions."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKER = Path(sys.argv.pop(1)).resolve()
BWRAP = Path("/usr/local/libexec/spatial-bwrap")

class CopyTreeWorkerTests(unittest.TestCase):
    def run_worker(self, source, destination, quota=1 << 20, entries=100):
        return subprocess.run(
            [str(BWRAP), "--unshare-all", "--unshare-user", "--disable-userns",
             "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv",
             "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib",
             "--ro-bind", "/usr/lib64", "/usr/lib64", "--ro-bind", str(WORKER), "/copy-worker",
             "--dir", "/source", "--dir", "/destination", "--ro-bind", str(source), "/source",
             "--bind", str(destination), "/destination", "--remount-ro", "/", "--",
             "/copy-worker", str(quota), str(entries)],
            capture_output=True, timeout=15)

    def test_positive_copy_preserves_bytes_without_sharing_inodes(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            (source / "sub").mkdir()
            (source / "sub" / "data").write_bytes(b"immutable snapshot bytes")
            result = self.run_worker(source, destination)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((destination / "sub" / "data").read_bytes(), b"immutable snapshot bytes")
            self.assertNotEqual((source / "sub" / "data").stat().st_ino,
                                (destination / "sub" / "data").stat().st_ino)
            self.assertEqual((source / "sub" / "data").read_bytes(), b"immutable snapshot bytes")

    def test_tree_digest_is_independent_of_directory_iteration_order(self):
        with tempfile.TemporaryDirectory() as temp:
            left = Path(temp) / "left"; right = Path(temp) / "right"
            out_left = Path(temp) / "out-left"; out_right = Path(temp) / "out-right"
            for path in (left, right, out_left, out_right): path.mkdir()
            for name in ("zeta", "alpha", "middle"): (left / name).write_bytes(name.encode())
            for name in ("middle", "alpha", "zeta"): (right / name).write_bytes(name.encode())
            first = self.run_worker(left, out_left); second = self.run_worker(right, out_right)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout.split()[-1], second.stdout.split()[-1])

    def test_symlink_special_file_cross_root_link_and_quota_are_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            (source / "escape").symlink_to("/etc/passwd")
            self.assertNotEqual(self.run_worker(source, destination).returncode, 0)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            os.mkfifo(source / "fifo")
            self.assertNotEqual(self.run_worker(source, destination).returncode, 0)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            file = source / "linked"; file.write_bytes(b"linked")
            os.link(file, Path(temp) / "outside-link")
            self.assertNotEqual(self.run_worker(source, destination).returncode, 0)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            (source / "large").write_bytes(b"12345")
            self.assertNotEqual(self.run_worker(source, destination, quota=4).returncode, 0)

    def test_entry_limit_and_existing_destination_entry_are_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            (source / "one").write_bytes(b"1"); (source / "two").write_bytes(b"2")
            self.assertNotEqual(self.run_worker(source, destination, entries=1).returncode, 0)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            (source / "same").write_bytes(b"source"); (destination / "same").write_bytes(b"destination")
            self.assertNotEqual(self.run_worker(source, destination).returncode, 0)
            self.assertEqual((destination / "same").read_bytes(), b"destination")

if __name__ == "__main__":
    unittest.main()
