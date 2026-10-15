"""Bounded immutable storage-owner snapshots on a real Btrfs volume."""
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from google.protobuf import text_format
import broker_pb2 as broker
import storage_pb2 as storage
import copy_pb2 as copy

PROBE = Path(sys.argv.pop(1)).resolve()
SOURCE = Path(sys.argv.pop(1)).resolve()


class StorageSnapshotTests(unittest.TestCase):
    def setUp(self):
        evidence = Path(os.environ["SPATIAL_SECURITY_EVIDENCE"])
        self.assertTrue(evidence.is_absolute(), "Snapshot tests require a private evidence directory")
        self.root = evidence / self._testMethodName
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.root.chmod(0o700)
        self.name = "m07-snap-" + uuid.uuid4().hex[:18]
        self.entry = Path("/var/lib/spatial/volumes") / self.name
        self.volume = self.entry / "mount"
        self.definition_path = self.root / "definition.textproto"
        definition = broker.StorageVolumeDefinition()
        text_format.Parse((SOURCE / "config/operator-storage.textproto").read_text(), definition)
        definition.name = self.name
        policy = definition.owner_policy
        policy.broker_uid = os.getuid()
        policy.volume_bytes = 512 * 2**20
        policy.control_plane_bytes = 64 * 2**20
        policy.filesystem_reserve_bytes = 64 * 2**20
        policy.default_policy.total_bytes = 8 * 2**20
        policy.default_policy.actor_state_default_bytes = 4 * 2**20
        for profile in policy.default_policy.profiles:
            profile.state_bytes = 4 * 2**20
        self.definition_path.write_text(text_format.MessageToString(definition))
        self.create_attempted = False
        self.owner = None
        self.owner_generation = 0
        self.owner_pid = None
        self.owner_start_time = None
        self.evidence_path = self.root / "evidence.json"
        self.owner_config = self.root / "owner.pb"
        self.sequence = 0
        self.addCleanup(self.cleanup)
        self.create_attempted = True
        self.provision("create")
        raw = subprocess.run(["sudo", "-n", "cat", str(self.entry / "owner.pb")],
                             check=True, capture_output=True, timeout=5).stdout
        config = storage.StorageOwnerConfig.FromString(raw)
        config.broker_uid = os.getuid()
        config.broker_pid = os.getpid()
        config.default_policy.total_bytes = 8 * 2**20
        config.default_policy.actor_state_default_bytes = 4 * 2**20
        for profile in config.default_policy.profiles:
            profile.state_bytes = 4 * 2**20
        catalog = config.copy_catalog
        catalog.protocol_version = 1
        catalog.revision = 5
        for data_class, kind, object_id, schema in (
            (copy.PROJECT_TREE, storage.StorageObject.PROJECT, "c"*32, "spatial.workspace.project-tree"),
            (copy.GIT_HISTORY, storage.StorageObject.REPOSITORY, "d"*32, "spatial.workspace.git-history"),
        ):
            entry = catalog.entries.add(workspace_id="b"*32, data_class=data_class,
                                        schema_id=schema, schema_version=1,
                                        maximum_bytes=4*2**20, maximum_entries=100,
                                        consistency=1)
            entry.source_object.kind = kind
            entry.source_object.id = object_id
        profile = config.copy_destination_profiles.add(
            name="copy-default", revision=7, maximum_total_bytes=8*2**20,
            maximum_entries=200, allowed_classes=[copy.PROJECT_TREE, copy.GIT_HISTORY],
            execution_resource_profile="development", application_profile="development")
        profile.storage_policy.CopyFrom(config.default_policy)
        profile.storage_policy.ClearField("workspace")
        profile.storage_policy.ClearField("actors")
        profile.storage_policy.total_bytes = 8*2**20
        profile.storage_policy.actor_state_default_bytes = 4*2**20
        self.owner_config.write_bytes(config.SerializeToString(deterministic=True))
        self.owner_config.chmod(0o600)
        self.start_owner()

    def provision(self, operation):
        command = ["sudo", "-n", sys.executable, "-B", str(SOURCE / "tools/provision_storage.py"),
                   operation, "--bindings-dir", str(PROBE.parent / "python")]
        command += ["--definition", str(self.definition_path)] if operation == "create" else ["--name", self.name]
        if operation != "inspect":
            command.append("--apply")
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def record_evidence(self, section, key, **fields):
        try:
            data = json.loads(self.evidence_path.read_text())
        except FileNotFoundError:
            data = {"test": self._testMethodName, "owners": {}, "cleanup": []}
        if section == "owners":
            entry = data.setdefault("owners", {}).setdefault(str(key), {})
            entry.update(fields)
        else:
            data.setdefault(section, []).append({"key": key, **fields})
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root,
                                         prefix=".evidence-", delete=False) as output:
            json.dump(data, output, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o600)
            temporary = Path(output.name)
        os.replace(temporary, self.evidence_path)
        self.evidence_path.chmod(0o600)

    @staticmethod
    def process_start_time(pid):
        try:
            contents = Path(f"/proc/{pid}/stat").read_text()
        except FileNotFoundError:
            return None
        close = contents.rfind(")")
        if close < 0:
            raise ValueError(f"Malformed process identity for PID {pid}")
        fields = contents[close + 2:].split()
        return fields[19]

    def owner_diagnostics(self):
        path = self.root / f"owner-{self.owner_generation}.stderr.log"
        try:
            with path.open("rb") as source:
                source.seek(0, os.SEEK_END)
                size = source.tell()
                source.seek(max(0, size - 4096))
                return source.read(4096).decode(errors="replace")
        except FileNotFoundError:
            return "(owner stderr log is unavailable)"

    def start_owner(self):
        self.owner_generation += 1
        stderr_path = self.root / f"owner-{self.owner_generation}.stderr.log"
        stderr_path.touch(mode=0o600, exist_ok=True)
        stderr_path.chmod(0o600)
        stderr_file = stderr_path.open("ab", buffering=0)
        try:
            try:
                self.owner = subprocess.Popen(
                    [
                        "sudo", "-n", "env",
                        "ASAN_OPTIONS=detect_leaks=1:abort_on_error=1",
                        "UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1",
                        str(PROBE), str(self.owner_config),
                    ],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_file,
                    start_new_session=True)
            except Exception as error:
                self.record_evidence("owners", self.owner_generation,
                                     launch_pid=None, launch_error=repr(error),
                                     terminal_exit_code=None, owner_gone=None, uncertain=True)
                raise
        finally:
            stderr_file.close()
        self.owner_pid = None
        self.owner_start_time = None
        self.record_evidence("owners", self.owner_generation,
                             launch_pid=self.owner.pid, stderr_file=str(stderr_path),
                             terminal_exit_code=None, owner_gone=None, uncertain=True)
        request = storage.StorageOwnerRequest()
        request.hello.SetInParent()
        reply = self.call(request)
        self.assertTrue(reply.HasField("identity"), reply)
        self.owner_pid = reply.identity.process_id
        self.record_evidence("owners", self.owner_generation,
                             actual_owner_hello_pid=self.owner_pid, uncertain=True)
        self.owner_start_time = self.process_start_time(self.owner_pid)
        self.assertIsNotNone(self.owner_start_time, "owner Hello PID has no readable process identity")
        self.record_evidence("owners", self.owner_generation,
                             actual_owner_hello_pid=self.owner_pid,
                             actual_owner_start_time=self.owner_start_time,
                             uncertain=False)

    def stop_owner(self):
        owner = self.owner
        if owner is None:
            return
        failures = []
        try:
            owner.stdin.close()
        except Exception as error:
            failures.append(f"closing owner input failed: {error}")
        try:
            result = owner.wait(timeout=15)
        except subprocess.TimeoutExpired:
            failures.append("owner did not stop after stdin close")
            result = None
        except Exception as error:
            failures.append(f"waiting for owner failed: {error}")
            result = owner.poll()
        if result is None:
            try:
                os.killpg(owner.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError as error:
                failures.append(f"signalling owner process group failed: {error}")
            try:
                result = owner.wait(timeout=3)
            except subprocess.TimeoutExpired:
                failures.append("owner process group did not stop after SIGTERM")
                try:
                    os.killpg(owner.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as error:
                    failures.append(f"stopping owner process group failed: {error}")
                try:
                    result = owner.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    result = owner.poll()
                    failures.append("owner process group remains after bounded SIGKILL wait")
                except Exception as error:
                    result = owner.poll()
                    failures.append(f"waiting after SIGKILL failed: {error}")
            except Exception as error:
                result = owner.poll()
                failures.append(f"waiting after SIGTERM failed: {error}")
        if result not in (0, None):
            failures.append(f"owner exited with status {result}")
        owner_gone = None
        identity_error = None
        if self.owner_pid is not None and self.owner_start_time is not None:
            deadline = time.monotonic() + 2
            while True:
                try:
                    current = self.process_start_time(self.owner_pid)
                    owner_gone = current != self.owner_start_time
                except Exception as error:
                    identity_error = str(error)
                    owner_gone = False
                if owner_gone or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            if not owner_gone:
                failures.append("owner Hello process remains present after bounded shutdown")
                if identity_error:
                    failures.append("owner process identity check failed: " + identity_error)
        else:
            failures.append("actual owner Hello PID was never proven; process exit is uncertain")
        try:
            owner.stdout.close()
        except Exception as error:
            failures.append(f"closing owner output failed: {error}")
        self.owner = None
        uncertain = result is None or not owner_gone
        try:
            self.record_evidence("owners", self.owner_generation,
                                 terminal_exit_code=result, owner_gone=owner_gone,
                                 uncertain=uncertain, shutdown_errors=failures)
        except Exception as error:
            failures.append(f"writing owner evidence failed: {error}")
        if failures:
            raise AssertionError("; ".join(failures) + "\n" + self.owner_diagnostics())

    def cleanup(self):
        failures = []
        if self.owner is not None:
            try:
                self.stop_owner()
            except Exception as error:
                failures.append(f"owner shutdown: {error}")
        if self.create_attempted:
            unmount_error = None
            try:
                self.provision("unmount")
            except Exception as error:
                unmount_error = str(error)
                failures.append(f"volume unmount: {error}")
            inspection = None
            inspect_error = None
            try:
                inspection = json.loads(self.provision("inspect"))
                if inspection.get("status") != "verified" or inspection.get("mounted") is not False or inspection.get("loop") is not None:
                    raise AssertionError(f"volume inspect does not prove detached state: {inspection}")
            except Exception as error:
                inspect_error = str(error)
                failures.append(f"volume detached-state inspection: {error}")
            try:
                self.record_evidence("cleanup", self._testMethodName,
                                     unmount_error=unmount_error,
                                     detached_inspection=inspection,
                                     inspection_error=inspect_error,
                                     cleanup_uncertain=bool(unmount_error or inspect_error))
            except Exception as error:
                failures.append(f"writing cleanup evidence failed: {error}")
        if failures:
            raise AssertionError("\n".join(failures))

    def read_exact(self, count):
        result = bytearray()
        deadline = time.monotonic() + 10
        while len(result) < count:
            self.assertLess(time.monotonic(), deadline, "timed out reading private owner probe")
            if not select.select([self.owner.stdout], [], [], 0.05)[0]:
                continue
            chunk = os.read(self.owner.stdout.fileno(), count - len(result))
            self.assertTrue(chunk, "owner closed its response stream; " + self.owner_diagnostics())
            result.extend(chunk)
        return bytes(result)

    def call(self, request):
        self.sequence += 1
        request.protocol_version = 1
        request.request_id = self.sequence
        payload = request.SerializeToString(deterministic=True)
        self.owner.stdin.write(struct.pack("!I", len(payload)) + payload)
        self.owner.stdin.flush()
        size = struct.unpack("!I", self.read_exact(4))[0]
        self.assertLessEqual(size, 1024 * 1024)
        return storage.StorageOwnerReply.FromString(self.read_exact(size))

    def state(self, request):
        reply = self.call(request)
        self.assertTrue(reply.HasField("state"), reply)
        return reply.state

    def error(self, request, code):
        reply = self.call(request)
        self.assertTrue(reply.HasField("error"), reply)
        self.assertEqual(reply.error.code, getattr(storage.StorageOwnerError, code), reply.error)
        return reply.error

    def create_source(self, repo_id, project_id):
        repo = storage.StorageObject(kind=storage.StorageObject.REPOSITORY, id=repo_id)
        req = storage.StorageOwnerRequest()
        req.repository_prepare.object.CopyFrom(repo)
        self.state(req)
        project = storage.StorageObject(kind=storage.StorageObject.PROJECT, id=project_id)
        req = storage.StorageOwnerRequest()
        req.project_prepare.object.CopyFrom(project)
        req.project_prepare.repository.CopyFrom(repo)
        state = self.state(req)
        path = self.volume / "worktrees" / project_id
        return repo, project, state, path

    def prepare(self, source, snapshot_id, operation_id=None):
        snapshot = storage.StorageObject(kind=storage.StorageObject.SNAPSHOT, id=snapshot_id)
        operation = operation_id or uuid.uuid4().bytes
        prepare = storage.StorageSnapshotPrepare(source_object=source, snapshot_object=snapshot,
                                                   operation_id=operation)
        prepare.request_digest = hashlib.sha256(prepare.SerializeToString(deterministic=True)).digest()
        request = storage.StorageOwnerRequest()
        request.snapshot_prepare.CopyFrom(prepare)
        return request

    def limited_application_source(self):
        repository, project, project_state, _ = self.create_source(uuid.uuid4().hex, uuid.uuid4().hex)
        workspace = uuid.uuid4().hex
        bind = storage.StorageOwnerRequest()
        bind.workspace_bind.workspace = workspace
        bind.workspace_bind.repository.CopyFrom(repository)
        bind.workspace_bind.project.CopyFrom(project)
        self.state(bind)
        source = storage.StorageObject(kind=storage.StorageObject.APPLICATION_STATE, id=uuid.uuid4().hex)
        prepare = storage.StorageOwnerRequest()
        prepare.application_prepare.workspace = workspace
        prepare.application_prepare.object.CopyFrom(source)
        prepare.application_prepare.fixture_actor = "limited-snapshot-source"
        prepare.application_prepare.profile = "development"
        state = self.state(prepare)
        self.assertEqual(state.maximum_bytes, 4*2**20)
        marker = self.volume / "applications" / source.id / "snapshot-witness"
        marker.write_bytes(b"latest real application state")
        return source, state, project_state.quota_group

    def quota_limits(self, groups):
        # Independently read exact kernel limit items through TREE_SEARCH.
        # The Linux UAPI search buffer is 4096 bytes, including its 104-byte key.
        code = r"""
import fcntl,json,os,struct,sys
fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
result={}
try:
    for group in map(int,sys.argv[2:]):
        data=bytearray(4096)
        struct.pack_into('=7Q4I4Q',data,0,8,0,0,group,group,0,2**64-1,244,244,1,0,0,0,0,0)
        fcntl.ioctl(fd,0xd0009411,data,True)
        assert struct.unpack_from('=I',data,64)[0]==1
        header=struct.unpack_from('=3Q2I',data,104)
        assert header[1:]==(0,group,244,40),header
        result[str(group)]=struct.unpack_from('<5Q',data,136)
finally:os.close(fd)
print(json.dumps(result,sort_keys=True))
"""
        result = subprocess.run(["sudo", "-n", sys.executable, "-B", "-c", code,
                                 str(self.volume), *map(str, groups)],
                                check=True, capture_output=True, text=True, timeout=5)
        return json.loads(result.stdout)

    def set_individual_quota(self, subvolume, maximum):
        subprocess.run(["sudo", "-n", "btrfs", "qgroup", "limit", str(maximum),
                        "0/" + str(subvolume), str(self.volume)],
                       check=True, capture_output=True, timeout=5)
        subprocess.run(["sudo", "-n", "btrfs", "filesystem", "sync", str(self.volume)],
                       check=True, capture_output=True, timeout=5)

    def rewind_pending_snapshot(self, snapshot_id):
        self.assertIsNone(self.owner)
        # Existing crash-boundary pattern: only rewind the stopped receipt.
        # The snapshot, UUID, parent, membership, and limit remain real kernel state.
        code = ("import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
                "c.execute(\"UPDATE snapshots SET snapshot_subvolume=0,snapshot_uuid=X'',state='pending' WHERE snapshot_id=?\",(sys.argv[2],)); "
                "c.execute(\"UPDATE objects SET subvolume=0,state='pending' WHERE kind=5 AND id=?\",(sys.argv[2],)); "
                "c.commit(); c.close()")
        subprocess.run(["sudo", "-n", sys.executable, "-B", "-c", code,
                        str(self.volume / ".storage-owner" / "catalog.db"), snapshot_id],
                       check=True, timeout=5)

    def test_limited_application_snapshot_clears_only_new_limit_and_retries_exactly(self):
        source, source_state, workspace_group = self.limited_application_source()
        groups = [source_state.subvolume_id, source_state.quota_group, workspace_group]
        before = self.quota_limits(groups)
        self.assertEqual(before[str(source_state.subvolume_id)], [1, 4*2**20, 0, 0, 0])
        request = self.prepare(source, uuid.uuid4().hex)
        ready = self.state(request)
        self.assertTrue(ready.read_only)
        self.assertEqual(self.quota_limits([ready.subvolume_id])[str(ready.subvolume_id)], [0]*5)
        self.assertEqual(self.quota_limits(groups), before, "source or parent quota changed")
        marker = self.volume / "snapshots" / request.snapshot_prepare.snapshot_object.id / "snapshot-witness"
        self.assertEqual(marker.read_bytes(), b"latest real application state")
        self.assertEqual(self.state(request).SerializeToString(), ready.SerializeToString())
        self.stop_owner()
        self.start_owner()
        self.assertEqual(self.state(request).SerializeToString(), ready.SerializeToString())
        self.assertEqual(self.quota_limits(groups), before)

    def test_pending_limited_snapshot_reconciles_before_and_after_quota_initialization(self):
        source, source_state, workspace_group = self.limited_application_source()
        groups = [source_state.subvolume_id, source_state.quota_group, workspace_group]
        before = self.quota_limits(groups)
        snapshot_id = uuid.uuid4().hex
        request = self.prepare(source, snapshot_id)
        ready = self.state(request)
        for inherited in (True, False):
            with self.subTest(interrupted_before_clear=inherited):
                self.stop_owner()
                if inherited:
                    self.set_individual_quota(ready.subvolume_id, source_state.maximum_bytes)
                self.rewind_pending_snapshot(snapshot_id)
                self.start_owner()
                recovered = self.state(request)
                self.assertEqual(recovered.subvolume_id, ready.subvolume_id)
                self.assertEqual(self.quota_limits([ready.subvolume_id])[str(ready.subvolume_id)], [0]*5)
                self.assertEqual(self.quota_limits(groups), before)
                self.assertEqual(self.state(request).SerializeToString(), recovered.SerializeToString())

    def test_unexpected_pending_snapshot_limit_is_rejected_without_repair(self):
        source, source_state, workspace_group = self.limited_application_source()
        groups = [source_state.subvolume_id, source_state.quota_group, workspace_group]
        before = self.quota_limits(groups)
        snapshot_id = uuid.uuid4().hex
        request = self.prepare(source, snapshot_id)
        ready = self.state(request)
        self.stop_owner()
        self.set_individual_quota(ready.subvolume_id, 2*2**20)
        self.rewind_pending_snapshot(snapshot_id)
        self.start_owner()
        error = self.error(request, "UNCERTAIN")
        self.assertIn("unexpected individual quota", error.message)
        self.assertEqual(self.quota_limits([ready.subvolume_id])[str(ready.subvolume_id)], [1, 2*2**20, 0, 0, 0])
        self.assertEqual(self.quota_limits(groups), before)
        self.error(request, "UNCERTAIN")

    def test_ready_snapshot_tampered_limit_is_rejected_without_repair(self):
        source, source_state, workspace_group = self.limited_application_source()
        groups = [source_state.subvolume_id, source_state.quota_group, workspace_group]
        before = self.quota_limits(groups)
        request = self.prepare(source, uuid.uuid4().hex)
        ready = self.state(request)
        self.stop_owner()
        self.set_individual_quota(ready.subvolume_id, 2*2**20)
        self.start_owner()
        inspect = storage.StorageOwnerRequest()
        inspect.snapshot_inspect.snapshot_object.CopyFrom(request.snapshot_prepare.snapshot_object)
        self.error(inspect, "UNCERTAIN")
        self.error(request, "UNCERTAIN")
        self.assertEqual(self.quota_limits([ready.subvolume_id])[str(ready.subvolume_id)], [1, 2*2**20, 0, 0, 0])
        self.assertEqual(self.quota_limits(groups), before)

    def test_readonly_capture_is_idempotent_digest_bound_and_source_independent(self):
        repo_id, project_id, snapshot_id = (uuid.uuid4().hex for _ in range(3))
        repo, source, _, source_path = self.create_source(repo_id, project_id)
        marker = source_path / "capture.bin"
        original = bytes(range(256)) * 512
        marker.write_bytes(original)
        request = self.prepare(source, snapshot_id)
        state = self.state(request)
        snapshot_path = self.volume / "snapshots" / snapshot_id
        snapshot_marker = snapshot_path / "capture.bin"
        self.assertTrue(state.read_only)
        self.assertEqual(state.source_object, source)
        self.assertEqual(state.operation_digest, request.snapshot_prepare.request_digest)
        self.assertGreater(state.referenced_bytes, 0)
        self.assertGreaterEqual(state.exclusive_bytes, 0)
        self.assertEqual(marker.read_bytes(), original, "snapshot creation changed its source")
        with self.assertRaises(OSError) as failure:
            with snapshot_marker.open("ab") as output:
                output.write(b"forbidden")
        self.assertEqual(failure.exception.errno, errno.EROFS)
        marker.write_bytes(b"new source bytes")
        self.assertEqual(snapshot_marker.read_bytes(), original)
        repeated = self.state(request)
        self.assertEqual(repeated.subvolume_id, state.subvolume_id)
        self.assertEqual(repeated.referenced_bytes, state.referenced_bytes)
        changed_digest = storage.StorageOwnerRequest()
        changed_digest.CopyFrom(request)
        changed_digest.snapshot_prepare.request_digest = b"x" * 32
        self.error(changed_digest, "CONFLICT")
        _, other_source, _, _ = self.create_source(repo_id, uuid.uuid4().hex)
        changed_source = storage.StorageOwnerRequest()
        changed_source.CopyFrom(request)
        changed_source.snapshot_prepare.source_object.CopyFrom(other_source)
        self.error(changed_source, "CONFLICT")
        missing = self.prepare(storage.StorageObject(kind=storage.StorageObject.PROJECT, id=uuid.uuid4().hex),
                               uuid.uuid4().hex)
        self.error(missing, "UNAVAILABLE")
        bad_path = self.prepare(storage.StorageObject(kind=storage.StorageObject.PROJECT, id="../escape".ljust(32, "a")),
                                uuid.uuid4().hex)
        self.error(bad_path, "INVALID_ARGUMENT")

    def test_snapshot_count_ceiling_and_receipt_checked_retirement(self):
        repo, source, _, source_path = self.create_source(uuid.uuid4().hex, uuid.uuid4().hex)
        (source_path / "retire.bin").write_bytes(b"retirement witness")
        first, second, third = (uuid.uuid4().hex for _ in range(3))
        first_request = self.prepare(source, first)
        second_request = self.prepare(source, second)
        first_state = self.state(first_request)
        second_state = self.state(second_request)
        self.assertTrue(first_state.read_only and second_state.read_only)
        self.error(self.prepare(source, third), "EXHAUSTED")
        unregistered = storage.StorageOwnerRequest()
        unregistered.snapshot_retire.snapshot_object.CopyFrom(
            storage.StorageObject(kind=storage.StorageObject.SNAPSHOT, id=uuid.uuid4().hex))
        unregistered.snapshot_retire.operation_id = uuid.uuid4().bytes
        self.error(unregistered, "UNAVAILABLE")
        retire = storage.StorageOwnerRequest()
        retire.snapshot_retire.snapshot_object.CopyFrom(first_request.snapshot_prepare.snapshot_object)
        retire.snapshot_retire.operation_id = uuid.uuid4().bytes
        retired = self.state(retire)
        self.assertFalse(retired.read_only)
        self.assertFalse((self.volume / "snapshots" / first).exists())
        self.assertTrue((self.volume / "snapshots" / second).exists())
        repeated = self.state(retire)
        self.assertEqual(repeated.subvolume_id, first_state.subvolume_id)
        reused_retire = storage.StorageOwnerRequest()
        reused_retire.snapshot_retire.snapshot_object.CopyFrom(second_request.snapshot_prepare.snapshot_object)
        reused_retire.snapshot_retire.operation_id = retire.snapshot_retire.operation_id
        self.error(reused_retire, "CONFLICT")
        second_inspect = storage.StorageOwnerRequest()
        second_inspect.snapshot_inspect.snapshot_object.CopyFrom(
            second_request.snapshot_prepare.snapshot_object)
        second_after_conflict = self.state(second_inspect)
        self.assertTrue(second_after_conflict.read_only)
        self.assertEqual(second_after_conflict.subvolume_id, second_state.subvolume_id)
        self.assertEqual((self.volume / "snapshots" / second).exists(), True)
        self.assertEqual((self.volume / "snapshots" / second / "retire.bin").read_bytes(),
                         b"retirement witness")
        self.error(storage.StorageOwnerRequest(snapshot_inspect=storage.StorageSnapshotInspect(
            snapshot_object=first_request.snapshot_prepare.snapshot_object)), "UNAVAILABLE")
        third_state = self.state(self.prepare(source, third))
        self.assertTrue(third_state.read_only)
        self.error(self.prepare(source, uuid.uuid4().hex), "EXHAUSTED")

    def test_restart_reconciles_pre_receipt_create_and_quarantines_identity_mismatch(self):
        repo, source, source_state, source_path = self.create_source(uuid.uuid4().hex, uuid.uuid4().hex)
        (source_path / "recovery.bin").write_bytes(b"crash boundary")
        first = uuid.uuid4().hex
        request = self.prepare(source, first)
        ready = self.state(request)
        self.stop_owner()
        # Recreate the durable catalog state immediately after Btrfs creation and
        # before the ready receipt transaction. The external snapshot remains real.
        code = ("import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
                "c.execute(\"UPDATE snapshots SET snapshot_subvolume=0,snapshot_uuid=X'',state='pending' WHERE snapshot_id=?\",(sys.argv[2],)); "
                "c.execute(\"UPDATE objects SET subvolume=0,state='pending' WHERE kind=5 AND id=?\",(sys.argv[2],)); "
                "c.commit(); c.close()")
        subprocess.run(["sudo", "-n", sys.executable, "-B", "-c", code,
                        str(self.volume / ".storage-owner" / "catalog.db"), first], check=True, timeout=5)
        self.start_owner()
        recovered = self.state(request)
        self.assertEqual(recovered.subvolume_id, ready.subvolume_id)
        second = uuid.uuid4().hex
        second_request = self.prepare(source, second)
        second_state = self.state(second_request)
        self.stop_owner()
        code = ("import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
                "c.execute(\"UPDATE snapshots SET snapshot_uuid=? WHERE snapshot_id=?\",(bytes.fromhex(sys.argv[3]),sys.argv[2])); "
                "c.commit(); c.close()")
        subprocess.run(["sudo", "-n", sys.executable, "-B", "-c", code,
                        str(self.volume / ".storage-owner" / "catalog.db"), second,
                        '00' * 16], check=True, timeout=5)
        self.start_owner()
        inspect = storage.StorageOwnerRequest()
        inspect.snapshot_inspect.snapshot_object.CopyFrom(second_request.snapshot_prepare.snapshot_object)
        self.error(inspect, "UNCERTAIN")
        self.assertTrue((self.volume / "snapshots" / second).exists())
        self.error(second_request, "UNCERTAIN")
        retire = storage.StorageOwnerRequest()
        retire.snapshot_retire.snapshot_object.CopyFrom(second_request.snapshot_prepare.snapshot_object)
        retire.snapshot_retire.operation_id = uuid.uuid4().bytes
        self.error(retire, "UNCERTAIN")

    def test_copy_catalog_rejects_unknown_schemas_and_unenforceable_ceilings(self):
        baseline = storage.StorageOwnerConfig.FromString(self.owner_config.read_bytes())
        mutations = []
        unknown_schema = storage.StorageOwnerConfig()
        unknown_schema.CopyFrom(baseline)
        unknown_schema.copy_catalog.entries[0].schema_id = "spatial.workspace.unknown"
        mutations.append((unknown_schema,
                          "Workspace copy class must map to a unique exact registered object"))
        oversized = storage.StorageOwnerConfig()
        oversized.CopyFrom(baseline)
        oversized.copy_catalog.entries[0].maximum_bytes = 8*2**30
        mutations.append((oversized,
                          "Invalid workspace copy catalog entry or unsupported consistency contract"))
        for index, (config, expected_diagnostic) in enumerate(mutations):
            with self.subTest(index=index):
                candidate = self.root / f"invalid-copy-policy-{index}.pb"
                candidate.write_bytes(config.SerializeToString(deterministic=True))
                candidate.chmod(0o600)
                result = subprocess.run(
                    ["sudo", "-n", "env", str(PROBE), str(candidate)],
                    capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(expected_diagnostic, result.stderr)
        self.assertIsNone(self.owner.poll(), "Rejected policy validation affected the live owner")

    def test_copy_catalog_query_binds_registered_objects_and_profile_revisions(self):
        workspace_id = "b"*32
        repository = storage.StorageObject(kind=storage.StorageObject.REPOSITORY, id="d"*32)
        project = storage.StorageObject(kind=storage.StorageObject.PROJECT, id="c"*32)
        prepare_repo = storage.StorageOwnerRequest()
        prepare_repo.repository_prepare.object.CopyFrom(repository)
        self.state(prepare_repo)
        prepare_project = storage.StorageOwnerRequest()
        prepare_project.project_prepare.object.CopyFrom(project)
        prepare_project.project_prepare.repository.CopyFrom(repository)
        self.state(prepare_project)
        bind = storage.StorageOwnerRequest()
        bind.workspace_bind.workspace = workspace_id
        bind.workspace_bind.repository.CopyFrom(repository)
        bind.workspace_bind.project.CopyFrom(project)
        self.state(bind)

        request = storage.StorageOwnerRequest()
        query = request.copy_catalog_query
        query.workspace_id = workspace_id
        query.destination_profile = "copy-default"
        query.expected_destination_profile_revision = 7
        query.expected_catalog_revision = 0
        query.data_classes.extend([copy.PROJECT_TREE, copy.GIT_HISTORY])
        discovered = self.call(request)
        self.assertTrue(discovered.HasField("copy_catalog"), discovered)
        self.assertEqual(discovered.copy_catalog.catalog_revision, 5)
        query.expected_catalog_revision = discovered.copy_catalog.catalog_revision
        result = self.call(request)
        self.assertTrue(result.HasField("copy_catalog"), result)
        self.assertEqual(result.copy_catalog.catalog_revision, 5)
        self.assertEqual(result.copy_catalog.destination_profile.revision, 7)
        self.assertEqual([entry.data_class for entry in result.copy_catalog.entries],
                         [copy.PROJECT_TREE, copy.GIT_HISTORY])
        self.assertEqual([entry.source_object.id for entry in result.copy_catalog.entries],
                         ["c"*32, "d"*32])
        self.assertEqual(self.call(request).copy_catalog, result.copy_catalog)

        changed_catalog = storage.StorageOwnerRequest()
        changed_catalog.copy_catalog_query.CopyFrom(query)
        changed_catalog.copy_catalog_query.expected_catalog_revision = 6
        self.error(changed_catalog, "CONFLICT")
        changed = storage.StorageOwnerRequest()
        changed.copy_catalog_query.CopyFrom(query)
        changed.copy_catalog_query.expected_destination_profile_revision = 8
        self.error(changed, "CONFLICT")
        denied = storage.StorageOwnerRequest()
        denied.copy_catalog_query.CopyFrom(query)
        denied.copy_catalog_query.workspace_id = "e"*32
        self.error(denied, "UNAVAILABLE")
        unsupported = storage.StorageOwnerRequest()
        unsupported.copy_catalog_query.CopyFrom(query)
        unsupported.copy_catalog_query.data_classes.append(copy.APPLICATION_STATE)
        self.error(unsupported, "UNAUTHORIZED")

    def test_copy_stage_claim_is_digest_bound_and_restart_is_uncertain(self):
        repo_id, project_id, snapshot_id, stage_id = (uuid.uuid4().hex for _ in range(4))
        repo, project, _, source_path = self.create_source(repo_id, project_id)
        (source_path / "copy.bin").write_bytes(b"owner stage source")
        snapshot_request = self.prepare(project, snapshot_id)
        snapshot_state = self.state(snapshot_request)
        stage = storage.StorageObject(kind=storage.StorageObject.COPY_STAGING, id=stage_id)
        prepare_stage = storage.StorageOwnerRequest()
        prepare_stage.copy_stage_prepare.object.CopyFrom(stage)
        prepare_stage.copy_stage_prepare.repository.CopyFrom(repo)
        destination_state = self.state(prepare_stage)
        self.assertFalse(destination_state.read_only)
        operation_id = uuid.uuid4().bytes
        begin = storage.StorageCopyStageBegin(
            snapshot_object=snapshot_request.snapshot_prepare.snapshot_object,
            destination_object=stage, operation_id=operation_id,
            request_digest=hashlib.sha256(b"copy request binding").digest(),
            maximum_bytes=1024 * 1024, maximum_entries=32)
        request = storage.StorageOwnerRequest()
        request.copy_stage_begin.CopyFrom(begin)
        prepared = self.call(request)
        self.assertTrue(prepared.HasField("copy_stage"), prepared)
        self.assertEqual(prepared.copy_stage.state, storage.StorageCopyStageReceipt.PREPARED)
        self.assertEqual(prepared.copy_stage.source_subvolume_id, snapshot_state.subvolume_id)
        self.assertEqual(prepared.copy_stage.destination_subvolume_id, destination_state.subvolume_id)
        self.assertEqual(self.call(request).copy_stage, prepared.copy_stage)
        changed = storage.StorageOwnerRequest()
        changed.CopyFrom(request)
        changed.copy_stage_begin.maximum_bytes += 1
        self.error(changed, "CONFLICT")

        start = storage.StorageOwnerRequest()
        start.copy_stage_start.operation_id = operation_id
        start.copy_stage_start.request_digest = begin.request_digest
        claimed = self.call(start).copy_stage
        self.assertTrue(claimed.claimed)
        self.assertEqual(claimed.state, storage.StorageCopyStageReceipt.COPYING)
        repeated = self.call(start).copy_stage
        self.assertFalse(repeated.claimed, "same operation must not launch a second worker")
        self.assertEqual(repeated.state, storage.StorageCopyStageReceipt.COPYING)

        self.stop_owner()
        self.start_owner()
        inspect = storage.StorageOwnerRequest()
        inspect.copy_stage_inspect.operation_id = operation_id
        inspect.copy_stage_inspect.request_digest = begin.request_digest
        uncertain = self.call(inspect).copy_stage
        self.assertEqual(uncertain.state, storage.StorageCopyStageReceipt.UNCERTAIN)
        self.assertFalse(self.call(start).copy_stage.claimed)


if __name__ == "__main__":
    unittest.main()
