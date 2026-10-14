"""Confined immutable tree-copy helper regressions."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKER = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(WORKER.parent / "python"))
from spatial.workspace.v1 import copy_worker_pb2
BWRAP = Path("/usr/local/libexec/spatial-bwrap")

def decode_result(result):
    if result.returncode != 0 or len(result.stdout) < 5:
        raise AssertionError(f"worker did not return a protobuf result: {result.stderr!r}")
    size = int.from_bytes(result.stdout[:4], "big")
    if size <= 0 or size > 4096 or len(result.stdout) != size + 4:
        raise AssertionError("worker frame length is invalid")
    message = copy_worker_pb2.WorkerResult()
    message.ParseFromString(result.stdout[4:])
    if message.protocol_version != 1 or message.WhichOneof("outcome") is None:
        raise AssertionError("worker result version or outcome is invalid")
    return message

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
            message = decode_result(result)
            self.assertEqual(message.WhichOneof("outcome"), "copied")
            self.assertEqual(message.copied.actual_bytes, len(b"immutable snapshot bytes"))
            self.assertEqual(message.copied.actual_entries, 2)
            self.assertEqual(len(message.copied.content_digest), 32)
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
            first_message = decode_result(first); second_message = decode_result(second)
            self.assertEqual(first_message.WhichOneof("outcome"), "copied")
            self.assertEqual(second_message.WhichOneof("outcome"), "copied")
            self.assertEqual(first_message.copied.content_digest, second_message.copied.content_digest)

    def test_symlink_special_file_cross_root_link_and_quota_are_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            (source / "escape").symlink_to("/etc/passwd")
            result = self.run_worker(source, destination)
            message = decode_result(result)
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_SYMLINK)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            os.mkfifo(source / "fifo")
            message = decode_result(self.run_worker(source, destination))
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_SPECIAL_ENTRY)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            file = source / "linked"; file.write_bytes(b"linked")
            os.link(file, Path(temp) / "outside-link")
            message = decode_result(self.run_worker(source, destination))
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_HARD_LINK)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            (source / "large").write_bytes(b"12345")
            message = decode_result(self.run_worker(source, destination, quota=4))
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_BYTE_LIMIT)

    def test_entry_limit_and_existing_destination_entry_are_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            (source / "one").write_bytes(b"1"); (source / "two").write_bytes(b"2")
            message = decode_result(self.run_worker(source, destination, entries=1))
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_ENTRY_LIMIT)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            (source / "same").write_bytes(b"source"); (destination / "same").write_bytes(b"destination")
            message = decode_result(self.run_worker(source, destination))
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_DESTINATION_CONFLICT)
            self.assertEqual((destination / "same").read_bytes(), b"destination")

if __name__ == "__main__":
    unittest.main()
