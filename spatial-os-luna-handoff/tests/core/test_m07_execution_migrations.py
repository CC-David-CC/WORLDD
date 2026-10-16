#!/usr/bin/env python3
"""Real-workspaced regressions for the historical execution schema-v7 upgrade."""
import hashlib
from contextlib import closing
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import copy_pb2 as copy_pb

WORKSPACED = pathlib.Path(sys.argv[1]).resolve()

V7_SCHEMA = """
CREATE TABLE repositories(id TEXT PRIMARY KEY,source TEXT NOT NULL,path TEXT UNIQUE NOT NULL,state TEXT NOT NULL);
CREATE TABLE worktrees(id TEXT PRIMARY KEY,repo TEXT NOT NULL REFERENCES repositories(id),branch TEXT NOT NULL,path TEXT UNIQUE NOT NULL,head TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE operations(id TEXT PRIMARY KEY,payload BLOB NOT NULL,result BLOB NOT NULL,worktree TEXT NOT NULL REFERENCES worktrees(id),state TEXT NOT NULL);
CREATE TABLE removals(id TEXT PRIMARY KEY,payload BLOB NOT NULL,record BLOB NOT NULL,worktree TEXT NOT NULL REFERENCES worktrees(id),retired TEXT UNIQUE NOT NULL,state TEXT NOT NULL);
CREATE TABLE workspaces(id TEXT PRIMARY KEY,worktree TEXT UNIQUE NOT NULL REFERENCES worktrees(id),repo TEXT UNIQUE NOT NULL REFERENCES repositories(id),policy INTEGER NOT NULL CHECK(policy>=0 AND policy<=30),revision INTEGER NOT NULL CHECK(revision>0));
CREATE TABLE workspace_members(workspace TEXT NOT NULL REFERENCES workspaces(id),actor TEXT NOT NULL,rights INTEGER NOT NULL CHECK(rights>=0 AND rights<=30),PRIMARY KEY(workspace,actor));
CREATE TABLE processes(id TEXT PRIMARY KEY,actor TEXT NOT NULL,operation TEXT NOT NULL,payload BLOB NOT NULL,workspace TEXT NOT NULL REFERENCES workspaces(id),rights INTEGER NOT NULL,state TEXT NOT NULL,exit_code INTEGER,UNIQUE(actor,operation));
CREATE TABLE copy_operations(operation_id BLOB PRIMARY KEY CHECK(length(operation_id)=16),requester BLOB NOT NULL CHECK(length(requester)=64),request BLOB NOT NULL,request_digest BLOB NOT NULL CHECK(length(request_digest)=32),source_workspace TEXT NOT NULL,source_authority_revision INTEGER NOT NULL CHECK(source_authority_revision>0),source_policy_revision INTEGER NOT NULL CHECK(source_policy_revision>0),destination_profile TEXT NOT NULL,destination_profile_revision INTEGER NOT NULL CHECK(destination_profile_revision>0),data_classes BLOB NOT NULL,phase INTEGER NOT NULL CHECK(phase BETWEEN 1 AND 9),error INTEGER NOT NULL DEFAULT 0,destination TEXT NOT NULL DEFAULT '',manifest BLOB NOT NULL DEFAULT X'',manifest_digest BLOB NOT NULL DEFAULT X'',phase_payload BLOB NOT NULL DEFAULT X'',authorization_binding BLOB NOT NULL DEFAULT X'',snapshot_receipts BLOB NOT NULL DEFAULT X'');
CREATE TABLE copy_effects(operation_id BLOB NOT NULL REFERENCES copy_operations(operation_id) ON DELETE CASCADE,effect_id BLOB NOT NULL CHECK(length(effect_id)=16),kind TEXT NOT NULL,request_digest BLOB NOT NULL CHECK(length(request_digest)=32),intent BLOB NOT NULL,state INTEGER NOT NULL CHECK(state IN(0,1)),receipt BLOB NOT NULL,PRIMARY KEY(operation_id,effect_id));
CREATE INDEX copy_effects_pending ON copy_effects(operation_id,state);
CREATE TABLE workspace_copy_policies(workspace TEXT PRIMARY KEY REFERENCES workspaces(id),binding BLOB NOT NULL);
PRAGMA user_version=7;
"""

TABLES = ("repositories", "worktrees", "operations", "removals", "workspaces",
          "workspace_members", "processes", "copy_operations", "copy_effects",
          "workspace_copy_policies")


def v7_request(op_id):
    # Use the build-generated binding so stored request bytes are canonical.
    request = copy_pb.WorkspaceCopyRequest(
        protocol_version=1,
        operation_id=op_id,
        source_workspace_id="workspace-old",
        expected_source_authority_revision=4,
        data_class=[copy_pb.PROJECT_TREE],
        destination_policy_profile="personal",
        expected_destination_profile_revision=1,
        expected_source_policy_revision=1,
    )
    return request.SerializeToString(deterministic=True)


def make_v7(root, *, orphan_member=False, conflicting_v8_table=False):
    root.mkdir(parents=True)
    db = sqlite3.connect(root / "execution.db")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(V7_SCHEMA)
    db.execute("INSERT INTO repositories VALUES(?,?,?,?)", ("repo-old", "origin", "/retained/repo", "ready"))
    db.execute("INSERT INTO worktrees VALUES(?,?,?,?,?,?)", ("tree-old", "repo-old", "main", "/retained/tree", "a" * 40, "ready"))
    db.execute("INSERT INTO operations VALUES(?,?,?,?,?)", ("git-op", b"payload", b"result", "tree-old", "complete"))
    db.execute("INSERT INTO removals VALUES(?,?,?,?,?,?)", ("remove-op", b"payload", b"record", "tree-old", "/retired/tree", "complete"))
    db.execute("INSERT INTO workspaces VALUES(?,?,?,?,?)", ("workspace-old", "tree-old", "repo-old", 3, 12))
    if orphan_member:
        db.commit()
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("INSERT INTO workspace_members VALUES(?,?,?)", ("missing-workspace", "actor-old", 3))
        db.execute("PRAGMA foreign_keys=ON")
    else:
        db.execute("INSERT INTO workspace_members VALUES(?,?,?)", ("workspace-old", "actor-old", 3))
    db.execute("INSERT INTO processes VALUES(?,?,?,?,?,?,?,?)", ("process-old", "actor-old", "launch-old", b"process", "workspace-old", 3, "exited", 0))
    db.execute("INSERT INTO workspace_copy_policies VALUES(?,?)", ("workspace-old", b"policy-binding"))
    for phase in range(1, 10):
        op_id = phase.to_bytes(16, "big")
        request = v7_request(op_id)
        digest = hashlib.sha256(request).digest()
        db.execute("INSERT INTO copy_operations(operation_id,requester,request,request_digest,source_workspace,source_authority_revision,source_policy_revision,destination_profile,destination_profile_revision,data_classes,phase,error,destination,manifest,manifest_digest,phase_payload,authorization_binding,snapshot_receipts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (op_id, b"r" * 64, request, digest, "workspace-old", 4, 1, "personal", 1, b"\x00\x01", phase, 0, "", b"", b"", b"", b"a" * 64, b""))
        if phase == 3:
            db.execute("INSERT INTO copy_effects VALUES(?,?,?,?,?,?,?)", (op_id, b"e" * 16, "snapshot", digest, b"intent-record", 0, b""))
        elif phase == 4:
            db.execute("INSERT INTO copy_effects VALUES(?,?,?,?,?,?,?)", (op_id, b"f" * 16, "allocate", digest, b"intent-record", 1, b"receipt-record"))
    if conflicting_v8_table:
        db.execute("CREATE TABLE configured_process_states(conflict TEXT)")
    db.commit()
    assert db.execute("PRAGMA foreign_key_check").fetchall() == ([] if not orphan_member else [("workspace_members", 1, "workspaces", 0)])
    db.close()


def snapshot(root):
    with closing(sqlite3.connect(root / "execution.db")) as db:
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        rows = {}
        for table in tables:
            rows[table] = sorted(db.execute('SELECT * FROM "' + table + '"').fetchall(), key=repr)
        return db.execute("PRAGMA user_version").fetchone()[0], schema, rows


class ExecutionV7Migration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m07-execution-v7-")
        self.root = pathlib.Path(self.temp.name) / "state"
        self.commands = []
        make_v7(self.root)

    def tearDown(self):
        # run() preserves the registry before cleaning this directory on failure.
        pass

    def assert_no_sanitizer_diagnostics(self, stdout, stderr):
        combined = (stdout or b"") + b"\n" + (stderr or b"")
        lowered = combined.lower()
        markers = (b"addresssanitizer", b"leaksanitizer", b"undefinedbehaviorsanitizer", b"runtime error:")
        found = [marker.decode("ascii") for marker in markers if marker in lowered]
        self.assertFalse(found, "sanitizer diagnostic in workspaced output: " + ", ".join(found))

    def invoke(self, *flags):
        argv = [str(WORKSPACED), "--state-dir", str(self.root), *flags]
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or b""
            stderr = error.stderr or b""
            self.commands.append({"argv": argv, "timeout_seconds": 20,
                                  "stdout": stdout[:4096].decode("utf-8", "replace"),
                                  "stderr": stderr[:4096].decode("utf-8", "replace")})
            self.assert_no_sanitizer_diagnostics(stdout, stderr)
            raise
        self.commands.append({"argv": argv, "returncode": result.returncode,
                              "stdout": result.stdout[:4096].decode("utf-8", "replace"),
                              "stderr": result.stderr[:4096].decode("utf-8", "replace")})
        self.assert_no_sanitizer_diagnostics(result.stdout, result.stderr)
        return result

    def run(self, result=None):
        result = result or self.defaultTestResult()
        failures_before = len(result.failures) + len(result.errors)
        result = super().run(result)
        failed = len(result.failures) + len(result.errors) > failures_before
        try:
            if failed:
                self.preserve_failure()
        finally:
            self.temp.cleanup()
        return result

    def preserve_failure(self):
        evidence = pathlib.Path(os.environ["SPATIAL_SECURITY_EVIDENCE"])
        self.assertTrue(evidence.is_absolute())
        evidence.mkdir(parents=True, exist_ok=True)
        evidence.chmod(0o700)
        case = evidence / (self.id().replace(".", "_") + "-" + self.temp.name.rsplit("/", 1)[-1])
        case.mkdir(parents=True, mode=0o700, exist_ok=False)
        db = self.root / "execution.db"
        if db.exists():
            with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(case / "execution.db")) as destination:
                source.backup(destination)
            (case / "execution.db").chmod(0o600)
            with closing(sqlite3.connect(db)) as connection:
                snap = {"user_version": connection.execute("PRAGMA user_version").fetchone()[0],
                        "schema": connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()}
                for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                    name = row[0]
                    snap[name] = connection.execute('SELECT * FROM "' + name + '"').fetchall()
            (case / "registry-snapshot.txt").write_text(repr(snap) + "\n", encoding="utf-8")
            (case / "registry-snapshot.txt").chmod(0o600)
        (case / "commands.json").write_text(json.dumps(self.commands, indent=2) + "\n", encoding="utf-8")
        (case / "commands.json").chmod(0o600)

    def test_v7_fixture_matches_historical_shape_and_retains_all_rows(self):
        version, schema, rows = snapshot(self.root)
        self.assertEqual(version, 7)
        self.assertEqual(set(rows), set(TABLES))
        self.assertNotIn("configured_application_states", rows)
        self.assertNotIn("configured_process_states", rows)
        self.assertEqual(len(rows["repositories"]), 1)
        self.assertEqual(len(rows["worktrees"]), 1)
        self.assertEqual(len(rows["operations"]), 1)
        self.assertEqual(len(rows["removals"]), 1)
        self.assertEqual(len(rows["workspaces"]), 1)
        self.assertEqual(len(rows["workspace_members"]), 1)
        self.assertEqual(len(rows["processes"]), 1)
        self.assertEqual(len(rows["copy_operations"]), 9)
        self.assertEqual(len(rows["copy_effects"]), 2)
        self.assertEqual(len(rows["workspace_copy_policies"]), 1)
        with closing(sqlite3.connect(self.root / "execution.db")) as db:
            self.assertEqual({r[1] for r in db.execute("PRAGMA table_info(copy_operations)")},
                             {"operation_id", "requester", "request", "request_digest", "source_workspace", "source_authority_revision", "source_policy_revision", "destination_profile", "destination_profile_revision", "data_classes", "phase", "error", "destination", "manifest", "manifest_digest", "phase_payload", "authorization_binding", "snapshot_receipts"})
            self.assertEqual({r[1] for r in db.execute("PRAGMA table_info(workspace_copy_policies)")}, {"workspace", "binding"})
        self.assertTrue(schema)

    def test_ordinary_startup_and_wrong_version_flags_refuse_without_mutation(self):
        before = snapshot(self.root)
        for flags in ((), ("--migrate-v6",), ("--migrate-v5",)):
            result = self.invoke(*flags)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            if not flags:
                self.assertIn(b"--migrate-v7", result.stderr)
            else:
                self.assertIn(b"Unknown execution schema; explicit migration required", result.stderr)
            self.assertEqual(snapshot(self.root), before)

    def test_exact_v7_migration_adds_only_schema8_tables_and_preserves_journal(self):
        before = snapshot(self.root)
        migrated = self.invoke("--migrate-v7")
        self.assertEqual(migrated.returncode, 0, migrated.stdout + migrated.stderr)
        after = snapshot(self.root)
        self.assertEqual(after[0], 8)
        self.assertEqual(after[2]["repositories"], before[2]["repositories"])
        for table in TABLES:
            if table != "copy_operations":
                self.assertEqual(after[2][table], before[2][table], table)
        old_operations = {row[0]: row for row in before[2]["copy_operations"]}
        new_operations = {row[0]: row for row in after[2]["copy_operations"]}
        self.assertEqual(set(new_operations), set(old_operations))
        for operation_id, old in old_operations.items():
            new = new_operations[operation_id]
            self.assertEqual(new[:10] + new[12:], old[:10] + old[12:], operation_id)
        self.assertEqual(set(after[2]) - set(before[2]), {"configured_application_states", "configured_process_states"})
        phases = {row[0]: (row[10], row[11]) for row in after[2]["copy_operations"]}
        for phase in range(1, 7):
            self.assertEqual(phases[phase.to_bytes(16, "big")], (9, 6))
        for phase in range(7, 10):
            self.assertEqual(phases[phase.to_bytes(16, "big")], (phase, 0))
        self.assertEqual(after[2]["copy_effects"], before[2]["copy_effects"])
        before_schema = {(kind, name, table): sql for kind, name, table, sql in before[1]}
        after_schema = {(kind, name, table): sql for kind, name, table, sql in after[1]}
        self.assertTrue(set(before_schema.items()).issubset(set(after_schema.items())))
        self.assertEqual({n for t, n, _, _ in after[1] if t == "table"} - {n for t, n, _, _ in before[1] if t == "table"},
                         {"configured_application_states", "configured_process_states"})
        with closing(sqlite3.connect(self.root / "execution.db")) as db:
            self.assertEqual(db.execute("PRAGMA table_info(configured_application_states)").fetchall(),
                             [(0, "workspace", "TEXT", 1, None, 1), (1, "key", "TEXT", 1, None, 2), (2, "state_id", "TEXT", 1, None, 0), (3, "contract", "BLOB", 1, None, 0)])
            self.assertEqual(db.execute("PRAGMA table_info(configured_process_states)").fetchall(),
                             [(0, "process", "TEXT", 0, None, 1), (1, "workspace", "TEXT", 1, None, 0), (2, "key", "TEXT", 1, None, 0), (3, "state_id", "TEXT", 1, None, 0)])
            self.assertEqual(db.execute("SELECT count(*) FROM configured_application_states").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM configured_process_states").fetchone()[0], 0)
            fks = db.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(fks, [])

    def test_repeated_migration_is_idempotent_and_normal_startup_reopens(self):
        first = self.invoke("--migrate-v7")
        self.assertEqual(first.returncode, 0, first.stderr)
        after_first = snapshot(self.root)
        repeated = self.invoke("--migrate-v7")
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(snapshot(self.root), after_first)
        reopened = self.invoke()
        self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)
        self.assertEqual(snapshot(self.root), after_first)

    def test_foreign_key_corruption_refuses_without_partial_migration(self):
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="m07-execution-v7-corrupt-fk-")
        self.root = pathlib.Path(self.temp.name) / "state"
        make_v7(self.root, orphan_member=True)
        before = snapshot(self.root)
        result = self.invoke("--migrate-v7")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Execution registry reference failure", result.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_migration_ddl_conflict_rolls_back_first_schema8_table(self):
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="m07-execution-v7-rollback-")
        self.root = pathlib.Path(self.temp.name) / "state"
        make_v7(self.root, conflicting_v8_table=True)
        before = snapshot(self.root)
        result = self.invoke("--migrate-v7")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.root), before)
        self.assertNotIn("configured_application_states", snapshot(self.root)[2])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
