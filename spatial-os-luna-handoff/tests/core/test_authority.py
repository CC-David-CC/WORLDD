"""Exercise the production authority store through a thin protobuf fixture driver."""
import io
import os
import random
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import core_pb2 as core
import grants_pb2 as pb
import grants_probe_pb2 as probe
from harness.wire import frame, read_message

BINARY = Path(sys.argv.pop(1))


def actor(letter, issuer=b'i'):
    return pb.ActorRef(issuer_id=issuer*32, actor_id=letter*32)


A, B, C = actor(b'a'), actor(b'b'), actor(b'c')


class AuthorityFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='spatial-authority-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.state = self.directory/'state'
        self.state.mkdir(mode=0o700)
        self.domain = pb.Domain(kind=pb.WORLD, id='world-one')
        self.process = None
        self.serial = 0
        self.revision = 0
        self.start(True)
        self.addCleanup(self.stop)

    def start(self, initialize=False, domain=None):
        chosen = domain or self.domain
        self.log = (self.directory/f'process-{self.serial}.log').open('wb')
        self.serial += 1
        self.process = subprocess.Popen([str(BINARY), str(self.state), str(chosen.kind), chosen.id,
                                         'initialize' if initialize else 'open'],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        ready = self.call('check', probe.Check(caller=A, scope=self.scope(domain=chosen), right=pb.POLICY_ADMINISTER))
        self.assertEqual(ready.WhichOneof('outcome'), 'decision', ready)

    def stop(self):
        if self.process:
            self.process.stdin.close()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(); raise
            self.process.stdout.close(); self.log.close(); self.process = None

    def call(self, name, value, inactive=()):
        request = probe.ProbeRequest(protocol_version=1, request_id=77, inactive_actors=inactive)
        if name == 'get': request.get = value
        else: getattr(request, name).CopyFrom(value)
        self.process.stdin.write(frame(request)); self.process.stdin.flush()
        try: reply = read_message(self.process.stdout, probe.ProbeReply)
        except EOFError:
            self.process.wait(timeout=5)
            self.fail(f'Authority probe exited {self.process.returncode}: {Path(self.log.name).read_text()}')
        self.assertEqual(reply.protocol_version, 1)
        self.assertEqual(reply.request_id, 77)
        return reply

    def scope(self, *components, domain=None):
        return pb.Scope(domain=domain or self.domain, components=components)

    def terms(self, subject=A, scope=None, rights=(pb.WORLD_NAVIGATE,), delegable=None, depth=3):
        return pb.GrantTerms(subject=subject, scope=scope or self.scope(), rights=rights,
                             delegable_rights=rights if delegable is None and depth else delegable or (),
                             delegation_depth=depth)

    def root(self, terms=None):
        reply = self.call('install', probe.Install(terms=terms or self.terms(), expected_revision=self.revision))
        self.assertEqual(reply.WhichOneof('outcome'), 'record', reply)
        self.revision = reply.record.created_revision
        return reply.record

    def delegate(self, parent, terms=None, caller=A, inactive=()):
        return self.call('delegate', probe.Delegate(caller=caller, parent_id=parent.id,
                         terms=terms or self.terms(B, depth=2), expected_revision=self.revision), inactive)

    def child(self, parent, terms=None, caller=A):
        reply = self.delegate(parent, terms, caller)
        self.assertEqual(reply.WhichOneof('outcome'), 'record', reply)
        self.revision = reply.record.created_revision
        return reply.record

    def allowed(self, subject, scope=None, right=pb.WORLD_NAVIGATE, inactive=()):
        reply = self.call('check', probe.Check(caller=subject, scope=scope or self.scope(), right=right), inactive)
        self.assertEqual(reply.WhichOneof('outcome'), 'decision', reply)
        self.assertEqual(reply.decision.revision, self.revision)
        return reply.decision.allowed

    def denied(self, reply, code=core.FORBIDDEN):
        self.assertEqual(reply.WhichOneof('outcome'), 'error', reply)
        self.assertEqual(reply.error.code, code, reply)

    def revoke(self, record, caller=A):
        reply = self.call('revoke', probe.Revoke(caller=caller, id=record.id, expected_revision=self.revision))
        self.assertEqual(reply.WhichOneof('outcome'), 'record', reply)
        self.revision = reply.record.revoked_revision
        return reply.record


class AuthorityTests(AuthorityFixture):
    def test_empty_store_denies_and_root_is_explicit(self):
        self.assertFalse(self.allowed(A))
        root = self.root()
        self.assertEqual(len(root.id), 32)
        self.assertFalse(root.parent_id)
        self.assertFalse(root.HasField('issuer'))
        self.assertTrue(self.allowed(A))
        self.assertFalse(self.allowed(B))

    def test_records_and_decisions_survive_restart(self):
        root = self.root(); child = self.child(root)
        self.stop(); self.start()
        self.assertEqual(self.call('get', child.id).record, child)
        self.assertTrue(self.allowed(B))

    def test_parent_and_issuer_are_derived(self):
        root = self.root(); child = self.child(root)
        self.assertEqual(child.parent_id, root.id)
        self.assertEqual(child.issuer, A)
        self.assertNotEqual(child.id, root.id)

    def test_wrong_subject_cannot_delegate(self):
        self.denied(self.delegate(self.root(), caller=C))

    def test_same_actor_bytes_from_another_issuer_are_not_identity(self):
        root = self.root()
        self.assertFalse(self.allowed(actor(b'a', b'j')))
        self.denied(self.delegate(root, caller=actor(b'a', b'j')))

    def test_scope_is_component_wise(self):
        self.root(self.terms(scope=self.scope(b'room', b'cabinet')))
        self.assertTrue(self.allowed(A, self.scope(b'room', b'cabinet', b'child')))
        self.assertFalse(self.allowed(A, self.scope(b'room', b'cabinet-backup')))
        self.assertFalse(self.allowed(A, self.scope(b'room')))
        self.assertFalse(self.allowed(A, self.scope(b'room/cabinet')))

    def test_domain_kind_and_identity_do_not_alias(self):
        root = self.root()
        for domain in (pb.Domain(kind=pb.WORLD, id='world-one-backup'), pb.Domain(kind=pb.WORKSPACE, id='world-one')):
            with self.subTest(domain=domain):
                self.denied(self.delegate(root, self.terms(B, scope=self.scope(domain=domain), rights=(pb.POLICY_ADMINISTER,), depth=2)))

    def test_scope_widening_and_sibling_delegation_fail(self):
        root = self.root(self.terms(scope=self.scope(b'room')))
        for scope in (self.scope(), self.scope(b'room-two')):
            self.denied(self.delegate(root, self.terms(B, scope=scope, depth=2)))

    def test_rights_widening_fails(self):
        self.denied(self.delegate(self.root(), self.terms(B, rights=(pb.WORLD_OBSERVE,), depth=2)))

    def test_held_right_is_not_implicitly_delegable(self):
        root = self.root(self.terms(delegable=(), depth=0))
        self.assertTrue(self.allowed(A))
        self.denied(self.delegate(root, self.terms(B, depth=0)))

    def test_delegable_subset_is_explicit(self):
        root = self.root(self.terms(rights=(pb.WORLD_NAVIGATE, pb.WORLD_OBSERVE), delegable=(pb.WORLD_NAVIGATE,)))
        self.child(root)
        self.denied(self.delegate(root, self.terms(C, rights=(pb.WORLD_OBSERVE,), depth=1)))

    def test_child_cannot_retain_parent_depth(self):
        self.denied(self.delegate(self.root(), self.terms(B, depth=3)))

    def test_zero_depth_and_excess_depth_are_invalid(self):
        for terms in (self.terms(depth=33), self.terms(depth=0, delegable=(pb.WORLD_NAVIGATE,))):
            self.denied(self.call('install', probe.Install(terms=terms, expected_revision=0)), core.INVALID_ARGUMENT)

    def test_unknown_duplicate_and_unspecified_rights_fail(self):
        for rights in ((999,), (pb.RIGHT_UNSPECIFIED,), (pb.WORLD_NAVIGATE, pb.WORLD_NAVIGATE)):
            self.denied(self.call('install', probe.Install(terms=self.terms(rights=rights), expected_revision=0)), core.INVALID_ARGUMENT)

    def test_delegable_right_requires_held_right(self):
        self.denied(self.call('install', probe.Install(terms=self.terms(delegable=(pb.WORLD_OBSERVE,)), expected_revision=0)), core.INVALID_ARGUMENT)

    def test_world_cannot_grant_workspace_execution(self):
        self.denied(self.call('install', probe.Install(terms=self.terms(rights=(pb.PROCESS_EXECUTE,)), expected_revision=0)), core.INVALID_ARGUMENT)

    def test_administration_does_not_imply_other_rights_or_delegation(self):
        root = self.root(self.terms(rights=(pb.POLICY_ADMINISTER,), depth=0))
        self.assertTrue(self.allowed(A, right=pb.POLICY_ADMINISTER))
        self.assertFalse(self.allowed(A))
        self.denied(self.delegate(root, self.terms(B, rights=(pb.POLICY_ADMINISTER,), depth=0)))

    def test_administration_delegation_requires_explicit_permission(self):
        root = self.root(self.terms(rights=(pb.POLICY_ADMINISTER,)))
        self.child(root, self.terms(B, rights=(pb.POLICY_ADMINISTER,), depth=0))
        self.assertTrue(self.allowed(B, right=pb.POLICY_ADMINISTER))
        self.assertFalse(self.allowed(B))

    def test_grandchild_cannot_recover_removed_right(self):
        root = self.root(self.terms(rights=(pb.WORLD_NAVIGATE, pb.WORLD_OBSERVE)))
        child = self.child(root)
        self.denied(self.delegate(child, self.terms(C, rights=(pb.WORLD_OBSERVE,), depth=1), B))

    def test_parent_revocation_invalidates_all_descendants(self):
        root = self.root(); child = self.child(root); grandchild = self.child(child, self.terms(C, depth=1), B)
        self.assertTrue(self.allowed(C)); self.revoke(root)
        self.assertFalse(self.allowed(B)); self.assertFalse(self.allowed(C))
        self.denied(self.delegate(grandchild, self.terms(B, depth=0), C))
        self.stop(); self.start(); self.assertFalse(self.allowed(C))

    def test_independent_grants_survive_parent_revocation(self):
        root = self.root(); self.child(root)
        independent = self.root(self.terms(B, depth=0))
        self.revoke(root)
        self.assertTrue(self.allowed(B))
        reply = self.call('check', probe.Check(caller=B, scope=self.scope(), right=pb.WORLD_NAVIGATE))
        self.assertEqual(reply.decision.grant_id, independent.id)

    def test_sibling_rights_cannot_be_combined_for_delegation(self):
        one = self.root(self.terms(rights=(pb.WORLD_NAVIGATE,)))
        self.root(self.terms(rights=(pb.WORLD_OBSERVE,)))
        self.denied(self.delegate(one, self.terms(B, rights=(pb.WORLD_NAVIGATE, pb.WORLD_OBSERVE), depth=2)))

    def test_inactive_ancestor_blocks_descendants(self):
        root = self.root(); self.child(root)
        self.assertFalse(self.allowed(B, inactive=(A,)))
        self.denied(self.delegate(root, inactive=(A,)))
        self.assertTrue(self.allowed(B))

    def test_inactive_subject_cannot_act_or_receive_delegation(self):
        root = self.root()
        self.denied(self.delegate(root, inactive=(B,)))
        self.assertFalse(self.allowed(A, inactive=(A,)))

    def test_unrelated_actor_cannot_revoke(self):
        root = self.root()
        self.denied(self.call('revoke', probe.Revoke(caller=C, id=root.id, expected_revision=self.revision)))
        self.assertTrue(self.allowed(A))

    def test_issuer_can_revoke_child_and_subject_can_relinquish(self):
        root = self.root(); child = self.child(root)
        self.revoke(child)
        self.assertTrue(self.allowed(A)); self.assertFalse(self.allowed(B))
        self.revoke(root)
        self.assertFalse(self.allowed(A))

    def test_scoped_administrator_cannot_revoke_outside_scope(self):
        self.root(self.terms(C, scope=self.scope(b'room'), rights=(pb.POLICY_ADMINISTER,), depth=0))
        root = self.root(); self.child(root, self.terms(B, scope=self.scope(b'room'), depth=0))
        self.denied(self.call('revoke', probe.Revoke(caller=C, id=root.id, expected_revision=self.revision)))
        local = self.root(self.terms(B, scope=self.scope(b'room'), depth=0))
        self.revoke(local, C)

    def test_revision_conflict_preserves_records(self):
        root = self.root()
        self.denied(self.call('delegate', probe.Delegate(caller=A, parent_id=root.id, terms=self.terms(B, depth=2), expected_revision=0)), core.REVISION_CONFLICT)
        self.assertFalse(self.allowed(B))

    def test_failed_database_commit_rolls_back_revision_and_grant(self):
        root = self.root()
        with sqlite3.connect(self.state/'authority.db') as db:
            db.execute("CREATE TRIGGER fail_grant BEFORE INSERT ON grant_record BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END")
        self.denied(self.delegate(root), core.STORAGE_ERROR)
        self.assertFalse(self.allowed(B))
        with sqlite3.connect(self.state/'authority.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM grant_record').fetchone()[0], 1)
            db.execute('DROP TRIGGER fail_grant')
        self.child(root)

    def test_database_has_real_durable_parent_and_scope_records(self):
        root = self.root(); child = self.child(root)
        with sqlite3.connect(self.state/'authority.db') as db:
            records = [pb.GrantRecord.FromString(row[0]) for row in db.execute('SELECT record FROM grant_record')]
            self.assertEqual({x.id for x in records}, {root.id, child.id})
            self.assertEqual(next(x for x in records if x.id == child.id).parent_id, root.id)

    def test_unknown_fields_and_invalid_actor_are_rejected(self):
        terms = self.terms(); terms.MergeFromString(b'\xf8\x07\x01')
        self.denied(self.call('install', probe.Install(terms=terms, expected_revision=0)), core.INVALID_ARGUMENT)
        terms = self.terms(); terms.subject.actor_id = b'short'
        self.denied(self.call('install', probe.Install(terms=terms, expected_revision=0)), core.INVALID_ARGUMENT)

    def test_bounded_scopes(self):
        for components in ((b'',), (b'a'*257,), tuple(b'a' for _ in range(33))):
            self.denied(self.call('install', probe.Install(terms=self.terms(scope=self.scope(*components)), expected_revision=0)), core.INVALID_ARGUMENT)

    def test_missing_parent_is_not_an_implicit_root(self):
        fake = pb.GrantRecord(id=b'x'*32)
        self.denied(self.delegate(fake), core.NOT_FOUND)

    def test_wrong_domain_and_second_authority_cannot_open_store(self):
        argv = [str(BINARY), str(self.state), '1', 'world-one', 'open']
        duplicate = subprocess.run(argv, input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(duplicate.returncode, 0)
        self.stop()
        argv[3] = 'another-world'
        wrong = subprocess.run(argv, input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(wrong.returncode, 0)
        self.start(); self.assertFalse(self.allowed(A))

    def test_corrupt_parent_chain_fails_closed(self):
        root = self.root(); child = self.child(root)
        with sqlite3.connect(self.state/'authority.db') as db:
            altered = pb.GrantRecord(); altered.CopyFrom(child); altered.parent_id = altered.id
            db.execute('UPDATE grant_record SET record=? WHERE id=?', (altered.SerializeToString(), child.id))
        self.denied(self.call('check', probe.Check(caller=B, scope=self.scope(), right=pb.WORLD_NAVIGATE)), core.STORAGE_ERROR)

    def test_m07_right_values_are_domain_scoped_and_not_implicit(self):
        world_rights = tuple(range(pb.WORLD_ENTER, pb.REPRESENTATION_USE + 1))
        workspace_rights = tuple(range(pb.WORKSPACE_ACCESS, pb.WORKSPACE_COPY_ENVIRONMENT + 1))
        for right in world_rights:
            with self.subTest(domain='world', right=right):
                self.assertFalse(self.allowed(A, right=right))
        root = self.root(self.terms(rights=world_rights, delegable=(), depth=0))
        for right in world_rights:
            self.assertTrue(self.allowed(A, right=right))

        self.stop(); self.state = self.directory/'workspace-rights'; self.state.mkdir(mode=0o700)
        self.domain = pb.Domain(kind=pb.WORKSPACE, id='workspace-one'); self.start(True)
        for right in workspace_rights:
            with self.subTest(domain='workspace', right=right):
                self.assertFalse(self.allowed(A, right=right))
        self.root(self.terms(rights=workspace_rights, delegable=(), depth=0))
        for right in workspace_rights:
            self.assertTrue(self.allowed(A, right=right))

    def test_workspace_create_is_reserved_to_registry_and_unknown_rights_fail(self):
        self.stop(); self.state = self.directory/'ordinary-workspace'; self.state.mkdir(mode=0o700)
        self.domain = pb.Domain(kind=pb.WORKSPACE, id='workspace-one'); self.start(True)
        ordinary = self.terms(rights=(pb.WORKSPACE_CREATE,), delegable=(), depth=0)
        self.denied(self.call('install', probe.Install(terms=ordinary, expected_revision=self.revision)), core.INVALID_ARGUMENT)
        self.denied(self.call('install', probe.Install(terms=self.terms(rights=(99,), delegable=(), depth=0),
                                                       expected_revision=self.revision)), core.INVALID_ARGUMENT)
        self.stop(); self.state = self.directory/'workspace-registry'; self.state.mkdir(mode=0o700)
        self.domain = pb.Domain(kind=pb.WORKSPACE, id='workspace-registry'); self.start(True)
        root = self.root(self.terms(rights=(pb.WORKSPACE_CREATE,), delegable=(), depth=0))
        self.assertTrue(self.allowed(A, right=pb.WORKSPACE_CREATE))
        self.denied(self.call('install', probe.Install(terms=self.terms(scope=self.scope(b'child'),
                                                       rights=(pb.WORKSPACE_CREATE,), delegable=(), depth=0),
                                                       expected_revision=self.revision)), core.INVALID_ARGUMENT)
        self.denied(self.call('install', probe.Install(terms=self.terms(rights=(pb.WORKSPACE_ACCESS,), delegable=(), depth=0),
                                                       expected_revision=self.revision)), core.INVALID_ARGUMENT)

    def test_workspace_rights_remain_independent(self):
        self.stop(); self.state = self.directory/'workspace'; self.state.mkdir(mode=0o700)
        self.domain = pb.Domain(kind=pb.WORKSPACE, id='workspace-one'); self.start(True)
        root = self.root(self.terms(rights=(pb.WORKSPACE_ACCESS, pb.FILE_BROWSE, pb.FILE_READ), delegable=(pb.FILE_READ,)))
        self.child(root, self.terms(B, rights=(pb.FILE_READ,), depth=0))
        self.assertTrue(self.allowed(B, right=pb.FILE_READ))
        for right in (pb.WORKSPACE_ACCESS, pb.FILE_EDIT, pb.FILE_PERMANENT_DELETE, pb.APPLICATION_CONTROL, pb.PROCESS_EXECUTE):
            self.assertFalse(self.allowed(B, right=right))

    def test_generated_chains_attenuate_and_do_not_union(self):
        rng = random.Random(72851)
        for index in range(40):
            with self.subTest(index=index):
                components = tuple(rng.randbytes(8) for _ in range(rng.randrange(0, 6)))
                root = self.root(self.terms(scope=self.scope(*components), rights=(pb.WORLD_NAVIGATE, pb.WORLD_OBSERVE)))
                selected = rng.choice((pb.WORLD_NAVIGATE, pb.WORLD_OBSERVE))
                suffix = rng.randbytes(8)
                child = self.child(root, self.terms(B, scope=self.scope(*components, suffix), rights=(selected,), depth=2))
                self.child(child, self.terms(C, scope=self.scope(*components, suffix, b'leaf'), rights=(selected,), depth=1), B)
                other = pb.WORLD_NAVIGATE if selected == pb.WORLD_OBSERVE else pb.WORLD_OBSERVE
                self.denied(self.delegate(child, self.terms(C, scope=child.terms.scope, rights=(other,), depth=1), B))
                self.denied(self.delegate(child, self.terms(C, scope=root.terms.scope, rights=(selected,), depth=1), B))

    def test_changed_parent_policy_is_detected(self):
        root = self.root(); self.child(root)
        altered = pb.GrantRecord(); altered.CopyFrom(root); altered.terms.ClearField('delegable_rights')
        with sqlite3.connect(self.state/'authority.db') as db:
            db.execute('UPDATE grant_record SET record=? WHERE id=?', (altered.SerializeToString(), root.id))
        self.denied(self.call('check', probe.Check(caller=B, scope=self.scope(), right=pb.WORLD_NAVIGATE)), core.STORAGE_ERROR)

    def test_missing_parent_record_is_storage_failure(self):
        root = self.root(); self.child(root)
        with sqlite3.connect(self.state/'authority.db') as db: db.execute('DELETE FROM grant_record WHERE id=?', (root.id,))
        self.denied(self.call('check', probe.Check(caller=B, scope=self.scope(), right=pb.WORLD_NAVIGATE)), core.STORAGE_ERROR)

    def test_process_death_preserves_committed_grants(self):
        root = self.root(); self.child(root)
        self.process.kill(); self.process.wait(timeout=5); self.stop(); self.start()
        self.assertTrue(self.allowed(B))

    def test_unknown_database_schema_requires_explicit_migration(self):
        self.root(); self.stop()
        with sqlite3.connect(self.state/'authority.db') as db: db.execute('PRAGMA user_version=999')
        result = subprocess.run([str(BINARY), str(self.state), '1', self.domain.id, 'open'], input=b'', capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        with sqlite3.connect(self.state/'authority.db') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 999)

    def test_private_database_and_directory_are_required(self):
        self.root(); self.stop()
        argv = [str(BINARY), str(self.state), '1', self.domain.id, 'open']
        (self.state/'authority.db').chmod(0o644)
        self.assertNotEqual(subprocess.run(argv, input=b'', capture_output=True, timeout=5).returncode, 0)
        (self.state/'authority.db').chmod(0o600); self.state.chmod(0o755)
        self.assertNotEqual(subprocess.run(argv, input=b'', capture_output=True, timeout=5).returncode, 0)
        self.state.chmod(0o700); self.start(); self.assertTrue(self.allowed(A))

    def test_symlink_and_hardlink_databases_are_rejected(self):
        self.root(); self.stop()
        path = self.state/'authority.db'; saved = self.directory/'saved.db'; path.rename(saved)
        argv = [str(BINARY), str(self.state), '1', self.domain.id, 'open']
        path.symlink_to(saved)
        self.assertNotEqual(subprocess.run(argv, input=b'', capture_output=True, timeout=5).returncode, 0)
        path.unlink(); os.link(saved, path)
        self.assertNotEqual(subprocess.run(argv, input=b'', capture_output=True, timeout=5).returncode, 0)
        path.unlink(); saved.rename(path); self.start(); self.assertTrue(self.allowed(A))


if __name__ == '__main__': unittest.main(verbosity=2)
