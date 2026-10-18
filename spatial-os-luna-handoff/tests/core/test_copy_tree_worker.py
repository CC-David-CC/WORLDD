"""Confined immutable tree-copy helper regressions."""
import os
import resource
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKER = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(WORKER.parent / "python"))
from spatial.workspace.v1 import copy_worker_pb2
BWRAP = Path("/usr/local/libexec/spatial-bwrap")

def u64(value):
    return int(value).to_bytes(8, "big", signed=False)

def cabinet_digest(entries):
    import hashlib
    stream = bytearray()
    def field(value):
        stream.extend(u64(len(value)))
        stream.extend(value)
    field(b"spatial-cabinet-copy-v1")
    for relative_path, kind, mode, payload in entries:
        field(relative_path)
        stream.extend(kind)
        stream.extend(u64(mode & 0o777))
        if kind == b"F":
            stream.extend(u64(len(payload)))
            stream.extend(payload)
    return hashlib.sha256(stream).digest()

def decode_result(result):
    if any(marker in result.stderr for marker in (b'AddressSanitizer', b'LeakSanitizer', b'UndefinedBehaviorSanitizer', b'runtime error:')):
        raise AssertionError(f"sanitizer diagnostics from confined worker: {result.stderr!r}")
    if result.returncode != 0 or len(result.stdout) < 5:
        raise AssertionError(f"worker did not return a protobuf result: {result.stderr!r}")
    size = int.from_bytes(result.stdout[:4], "big")
    if size <= 0 or size > 4096 or len(result.stdout) != size + 4:
        raise AssertionError("worker frame length is invalid")
    message = copy_worker_pb2.WorkerResult()
    message.ParseFromString(result.stdout[4:])
    if message.WhichOneof("outcome") is None:
        raise AssertionError("worker result version or outcome is invalid")
    return message

class CopyTreeWorkerTests(unittest.TestCase):
    def run_worker(self, source, destination, quota=1 << 20, entries=100, fail_readdir_after=None, nofile_limit=None):
        request = copy_worker_pb2.WorkerRequest(
            protocol_version=1, maximum_bytes=quota, maximum_entries=entries)
        with tempfile.NamedTemporaryFile() as request_file:
            request_file.write(request.SerializeToString())
            request_file.flush()
            command = [str(BWRAP), "--unshare-all", "--unshare-user", "--disable-userns",
                       "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv",
                       "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib",
                       "--ro-bind", "/usr/lib64", "/usr/lib64", "--ro-bind", "/usr/share", "/usr/share",
                       "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
                       "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
                       "--tmpfs", "/tmp", "--setenv", "PATH", "/usr/bin:/bin",
                       "--setenv", "HOME", "/nonexistent", "--setenv", "TMPDIR", "/tmp",
                       "--setenv", "LC_ALL", "C", "--ro-bind", str(WORKER), "/copy-worker",
                       "--dir", "/source", "--dir", "/destination", "--ro-bind", str(source), "/source",
                       "--bind", str(destination), "/destination", "--ro-bind", request_file.name, "/job.pb"]
            for variable in ('ASAN_OPTIONS', 'UBSAN_OPTIONS'):
                if variable in os.environ:
                    command.extend(['--setenv', variable, os.environ[variable]])
            if fail_readdir_after is not None:
                command.extend(["--setenv", "SPATIAL_COPY_TEST_FAIL_READDIR_AFTER", str(fail_readdir_after)])
            command.extend(["--remount-ro", "/", "--", "/copy-worker"])
            def lower_nofile():
                if nofile_limit is not None:
                    resource.setrlimit(resource.RLIMIT_NOFILE, (nofile_limit, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
            return subprocess.run(command, capture_output=True, timeout=15,
                                  preexec_fn=lower_nofile if nofile_limit is not None else None)

    def run_cabinet_worker(self, source_parent, source_name, destination, destination_name,
                           source_is_directory, quota=1 << 20, entries=100):
        request = copy_worker_pb2.WorkerRequest(
            protocol_version=3, maximum_bytes=quota, maximum_entries=entries,
            cabinet_source_name=os.fsencode(source_name),
            cabinet_destination_name=os.fsencode(destination_name),
            cabinet_source_is_directory=source_is_directory)
        with tempfile.NamedTemporaryFile() as request_file:
            request_file.write(request.SerializeToString()); request_file.flush()
            command = [str(BWRAP), "--unshare-all", "--unshare-user", "--disable-userns",
                       "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv",
                       "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib",
                       "--ro-bind", "/usr/lib64", "/usr/lib64", "--ro-bind", "/usr/share", "/usr/share",
                       "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
                       "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
                       "--tmpfs", "/tmp", "--setenv", "PATH", "/usr/bin:/bin",
                       "--setenv", "HOME", "/nonexistent", "--setenv", "TMPDIR", "/tmp",
                       "--setenv", "LC_ALL", "C", "--ro-bind", str(WORKER), "/copy-worker",
                       "--dir", "/source", "--dir", "/destination",
                       "--ro-bind", str(source_parent), "/source", "--bind", str(destination), "/destination",
                       "--ro-bind", request_file.name, "/job.pb", "--remount-ro", "/", "--", "/copy-worker"]
            return subprocess.run(command, capture_output=True, timeout=15)

    def test_cabinet_protocol_copies_file_and_directory_with_canonical_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; destination = root / "destination"
            source.mkdir(); destination.mkdir(); (source / "nested").mkdir()
            file = source / "single"; file.write_bytes(b"root file")
            os.chmod(file, 0o751)
            file_message = decode_result(self.run_cabinet_worker(source, "single", destination,
                "copied-file", False))
            self.assertEqual(file_message.protocol_version, 3)
            self.assertEqual(file_message.WhichOneof("outcome"), "copied")
            self.assertEqual((destination / "copied-file").read_bytes(), b"root file")
            self.assertEqual((destination / "copied-file").stat().st_mode & 0o777, 0o751)
            (source / "nested" / "child").write_bytes(b"nested bytes")
            directory_message = decode_result(self.run_cabinet_worker(source, "nested", destination,
                "copied-tree", True))
            self.assertEqual(directory_message.WhichOneof("outcome"), "copied")
            self.assertEqual((destination / "copied-tree" / "child").read_bytes(), b"nested bytes")
            self.assertEqual(directory_message.copied.actual_entries, 2)
            expected_file = cabinet_digest([
                (b"", b"F", file.stat().st_mode, b"root file")])
            nested_mode = (source / "nested").stat().st_mode
            child_mode = (source / "nested" / "child").stat().st_mode
            expected_directory = cabinet_digest([
                (b"", b"D", nested_mode, b""),
                (b"child", b"F", child_mode, b"nested bytes")])
            self.assertEqual(file_message.copied.content_digest, expected_file)
            self.assertEqual(directory_message.copied.content_digest, expected_directory)

    def test_cabinet_protocol_rejects_nested_mounts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; destination = root / "destination"
            source.mkdir(); destination.mkdir()
            mountpoint = source / "mounted"; mountpoint.mkdir()
            outside = root / "outside"; outside.mkdir(); (outside / "payload").write_bytes(b"mounted")
            mounted = subprocess.run(["mount", "--bind", str(outside), str(mountpoint)],
                                     capture_output=True, timeout=5)
            if mounted.returncode != 0:
                self.skipTest("host does not permit a private bind-mount fixture")
            try:
                result = decode_result(self.run_cabinet_worker(source.parent, "source", destination,
                    "copy", True))
                self.assertEqual(result.WhichOneof("outcome"), "failure")
                self.assertEqual(result.failure.reason, copy_worker_pb2.FAILURE_SPECIAL_ENTRY)
            finally:
                subprocess.run(["umount", str(mountpoint)], check=True, timeout=5)

    def test_cabinet_copy_allows_same_parent_sibling_files_and_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "source-file").write_bytes(b"same parent")
            result = decode_result(self.run_cabinet_worker(root, "source-file", root,
                "copied-file", False))
            self.assertEqual(result.WhichOneof("outcome"), "copied")
            self.assertEqual((root / "copied-file").read_bytes(), b"same parent")
            source_tree = root / "tree"; source_tree.mkdir()
            (source_tree / "child").write_bytes(b"inside")
            result = decode_result(self.run_cabinet_worker(root, "tree", root,
                "copied-tree", True))
            self.assertEqual(result.WhichOneof("outcome"), "copied")
            self.assertEqual((root / "copied-tree" / "child").read_bytes(), b"inside")

    def test_cabinet_protocol_rejects_symlink_special_and_ceilings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; destination = root / "destination"
            source.mkdir(); destination.mkdir(); (source / "link").symlink_to("/etc/passwd")
            result = decode_result(self.run_cabinet_worker(source, "link", destination, "copy", False))
            self.assertEqual(result.WhichOneof("outcome"), "failure")
            self.assertEqual(result.failure.reason, copy_worker_pb2.FAILURE_SYMLINK)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; destination = root / "destination"
            source.mkdir(); destination.mkdir(); os.mkfifo(source / "fifo")
            result = decode_result(self.run_cabinet_worker(source, "fifo", destination, "copy", False))
            self.assertEqual(result.WhichOneof("outcome"), "failure")
            self.assertEqual(result.failure.reason, copy_worker_pb2.FAILURE_SPECIAL_ENTRY)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; destination = root / "destination"
            source.mkdir(); destination.mkdir(); (source / "large").write_bytes(b"12345")
            result = decode_result(self.run_cabinet_worker(source, "large", destination, "copy", False, quota=4))
            self.assertEqual(result.WhichOneof("outcome"), "failure")
            self.assertEqual(result.failure.reason, copy_worker_pb2.FAILURE_BYTE_LIMIT)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; destination = root / "destination"
            source.mkdir(); destination.mkdir(); (source / "file").write_bytes(b"source")
            (destination / "existing").write_bytes(b"keep")
            result = decode_result(self.run_cabinet_worker(source, "file", destination, "existing", False))
            self.assertEqual(result.WhichOneof("outcome"), "failure")
            self.assertEqual(result.failure.reason, copy_worker_pb2.FAILURE_DESTINATION_CONFLICT)
            self.assertEqual((destination / "existing").read_bytes(), b"keep")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; destination = root / "destination"
            source.mkdir(); destination.mkdir(); (source / "tree").mkdir()
            (destination / "existing").mkdir(); (destination / "existing" / "keep").write_bytes(b"keep")
            result = decode_result(self.run_cabinet_worker(source, "tree", destination, "existing", True))
            self.assertEqual(result.WhichOneof("outcome"), "failure")
            self.assertEqual(result.failure.reason, copy_worker_pb2.FAILURE_DESTINATION_CONFLICT)
            self.assertEqual((destination / "existing" / "keep").read_bytes(), b"keep")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "source"; destination = root / "destination"
            source.mkdir(); destination.mkdir(); (source / "tree").mkdir(); (source / "tree" / "child").touch()
            result = decode_result(self.run_cabinet_worker(source, "tree", destination, "copy", True, entries=1))
            self.assertEqual(result.WhichOneof("outcome"), "failure")
            self.assertEqual(result.failure.reason, copy_worker_pb2.FAILURE_ENTRY_LIMIT)

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

    def test_wide_tree_is_bounded_before_name_queue_growth(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            for index in range(2048):
                (source / f"entry-{index:04}").touch()
            message = decode_result(self.run_worker(source, destination, entries=8))
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_ENTRY_LIMIT)
            self.assertEqual(list(destination.iterdir()), [])

    def test_depth_128_succeeds_and_depth_129_has_typed_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            current = source
            for _ in range(128):
                current = current / "d"
                current.mkdir()
            message = decode_result(self.run_worker(source, destination, entries=1000))
            self.assertEqual(message.WhichOneof("outcome"), "copied")
            self.assertEqual(message.copied.actual_entries, 128)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            current = source
            for _ in range(129):
                current = current / "d"
                current.mkdir()
            message = decode_result(self.run_worker(source, destination, entries=1000))
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_DEPTH_LIMIT)

    def test_low_descriptor_limit_caps_supported_depth(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir()
            current = source
            for _ in range(20):
                current = current / "d"
                current.mkdir()
            message = decode_result(self.run_worker(
                source, destination, entries=1000, nofile_limit=64))
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_DESCRIPTOR_LIMIT)

    def test_readdir_error_is_reported_as_io_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"; destination = Path(temp) / "destination"
            source.mkdir(); destination.mkdir(); (source / "entry").touch()
            message = decode_result(self.run_worker(
                source, destination, fail_readdir_after=0))
            self.assertEqual(message.WhichOneof("outcome"), "failure")
            self.assertEqual(message.failure.reason, copy_worker_pb2.FAILURE_IO)
            self.assertEqual(list(destination.iterdir()), [])

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
