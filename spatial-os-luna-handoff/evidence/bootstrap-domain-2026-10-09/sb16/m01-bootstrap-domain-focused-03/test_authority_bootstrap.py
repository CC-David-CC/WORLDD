"""DRAFT: production domain binding and receipt counterexamples.

Move into tests/core only with the additive probe schema and fixture extraction.
Its baseline can run against the existing store with isolated proposed bindings.
It does not establish domain grant delivery.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path
import unittest

import core_pb2 as core
import grants_pb2 as pb
import grants_probe_pb2 as probe
from test_authority import AuthorityFixture, A, B, C, BINARY

MIGRATE = Path(sys.argv.pop(1))


class BootstrapAuthorityTests(AuthorityFixture):
    def migrate_command(self, kind='world', identity=None):
        return [str(MIGRATE), '--state-dir', str(self.state), '--domain-kind', kind,
                '--domain-id', identity or self.domain.id]

    def selector(self, revision=1, issuer=None, profile='starter', template='entry'):
        return pb.BootstrapSelector(issuer_id=issuer or B.issuer_id, profile_id=profile,
                                    profile_revision=revision, template_id=template)

    def template(self, **kwargs):
        terms = self.terms(B, depth=0, **kwargs)
        terms.ClearField('subject')
        return terms

    def bind(self, parent, selector=None, terms=None, caller=A, inactive=()):
        return self.call('bootstrap_bind', probe.BootstrapBind(caller=caller,
            selector=selector or self.selector(), parent_id=parent.id,
            terms=terms or self.template(), expected_revision=self.revision), inactive)

    def binding(self, parent=None, **kwargs):
        result = self.bind(parent or self.root(), **kwargs)
        self.assertEqual(result.WhichOneof('outcome'), 'binding', result)
        self.revision = result.binding.created_revision
        return result.binding

    def materialize(self, binding, recipient=B, selector=None, terms=None, inactive=()):
        return self.call('bootstrap_apply', probe.BootstrapApply(binding_id=binding.id,
            recipient=recipient, selector=selector or binding.selector,
            terms=terms or binding.terms), inactive)

    def applied(self, binding, **kwargs):
        result = self.materialize(binding, **kwargs)
        self.assertEqual(result.WhichOneof('outcome'), 'record', result)
        self.revision = max(self.revision, result.record.created_revision)
        return result.record

    def counts(self):
        with sqlite3.connect(self.state/'authority.db') as db:
            return tuple(db.execute('SELECT count(*) FROM '+table).fetchone()[0]
                         for table in ('grant_record','bootstrap_binding','bootstrap_receipt'))

    def test_binding_does_not_grant_until_authorized_materialization(self):
        parent = self.root(); binding = self.binding(parent)
        self.assertEqual(binding.authorizer, A)
        self.assertEqual(binding.parent_id, parent.id)
        self.assertFalse(binding.terms.HasField('subject'))
        self.assertFalse(self.allowed(B))
        grant = self.applied(binding)
        self.assertEqual(grant.parent_id, parent.id)
        self.assertEqual(grant.issuer, A)
        self.assertEqual(grant.terms.subject, B)
        self.assertTrue(self.allowed(B))
        self.assertEqual(self.counts(), (2,1,1))

    def test_binding_requires_parent_holder_and_current_ancestry(self):
        parent = self.root()
        self.denied(self.bind(parent, caller=C))
        self.denied(self.bind(parent, inactive=(A,)))
        self.revoke(parent)
        self.denied(self.bind(parent))
        self.assertEqual(self.counts(), (1,0,0))

    def test_administration_without_delegation_cannot_bind(self):
        parent = self.root(self.terms(rights=(pb.POLICY_ADMINISTER,), depth=0))
        self.denied(self.bind(parent))
        self.assertEqual(self.counts(), (1,0,0))

    def test_template_cannot_widen_scope_rights_or_delegation_depth(self):
        parent = self.root(self.terms(scope=self.scope(b'room-one'), depth=1))
        requests = [self.template(), self.template(scope=self.scope(b'room-two')),
                    self.template(scope=self.scope(b'room-one'), rights=(pb.WORLD_OBSERVE,))]
        deep = self.template(scope=self.scope(b'room-one')); deep.delegation_depth=1
        requests.append(deep)
        for terms in requests:
            with self.subTest(terms=terms): self.denied(self.bind(parent, terms=terms))
        binding = self.binding(parent, terms=self.template(scope=self.scope(b'room-one',b'object')))
        self.applied(binding)
        self.assertTrue(self.allowed(B, self.scope(b'room-one',b'object')))
        self.assertFalse(self.allowed(B, self.scope(b'room-one')))

    def test_binding_revision_conflict_does_not_change_store(self):
        parent = self.root(); before = self.revision
        request = probe.BootstrapBind(caller=A, selector=self.selector(), parent_id=parent.id,
            terms=self.template(), expected_revision=before-1)
        self.denied(self.call('bootstrap_bind', request), core.REVISION_CONFLICT)
        self.assertEqual(self.counts(), (1,0,0))
        self.assertTrue(self.allowed(A))

    def test_exact_retry_and_restart_preserve_grant_and_revision(self):
        binding = self.binding(); original = self.applied(binding); revision = self.revision
        for _ in range(2):
            self.assertEqual(self.applied(binding), original)
            self.assertEqual(self.revision, revision)
            self.stop(); self.start()
        self.assertEqual(self.applied(binding), original)
        self.assertEqual(self.counts(), (2,1,1))

    def test_retry_cannot_revive_revoked_grant(self):
        binding = self.binding(); grant = self.applied(binding)
        revoked = self.revoke(grant); revision = self.revision
        self.assertEqual(self.applied(binding), revoked)
        self.assertEqual(self.revision, revision)
        self.assertFalse(self.allowed(B))
        self.assertEqual(self.counts(), (2,1,1))

    def test_new_profile_revision_cannot_replace_existing_receipt(self):
        parent = self.root(); first = self.binding(parent); original = self.applied(first)
        second = self.binding(parent, selector=self.selector(revision=2))
        self.denied(self.materialize(second), core.REVISION_CONFLICT)
        self.assertEqual(self.call('get', original.id).record, original)
        self.assertEqual(self.counts(), (2,2,1))

    def test_different_actors_get_distinct_grants(self):
        binding = self.binding(); first = self.applied(binding)
        second = self.applied(binding, recipient=C)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.terms.subject, C)
        self.assertTrue(self.allowed(B)); self.assertTrue(self.allowed(C))
        self.assertEqual(self.counts(), (3,1,2))

    def test_checked_selector_and_terms_must_match_binding(self):
        binding = self.binding()
        for selector in (self.selector(revision=2), self.selector(profile='other'),
                         self.selector(template='other'), self.selector(issuer=b'x'*32)):
            with self.subTest(selector=selector): self.denied(self.materialize(binding, selector=selector))
        self.denied(self.materialize(binding, terms=self.template(rights=(pb.WORLD_OBSERVE,))))
        self.assertEqual(self.counts(), (1,1,0))

    def test_revoked_recipient_or_parent_cannot_receive_new_grant(self):
        binding = self.binding()
        self.denied(self.materialize(binding, inactive=(B,)))
        self.denied(self.materialize(binding, inactive=(A,)))
        parent = self.call('get', binding.parent_id).record
        self.revoke(parent)
        self.denied(self.materialize(binding))
        self.assertEqual(self.counts(), (1,1,0))

    def test_receipt_write_failure_rolls_back_grant_and_revision(self):
        binding = self.binding(); before = self.revision
        with sqlite3.connect(self.state/'authority.db') as db:
            db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON bootstrap_receipt BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.denied(self.materialize(binding), core.STORAGE_ERROR)
        self.assertEqual(self.counts(), (1,1,0)); self.assertFalse(self.allowed(B))
        self.assertEqual(self.revision, before)
        with sqlite3.connect(self.state/'authority.db') as db: db.execute('DROP TRIGGER fail_receipt')
        self.applied(binding); self.assertEqual(self.counts(), (2,1,1))

    def test_parent_revocation_preserves_independent_grant(self):
        first_parent = self.root(); binding = self.binding(first_parent)
        bootstrap = self.applied(binding)
        second_parent = self.root(); independent = self.child(second_parent, self.terms(B,depth=0))
        self.revoke(first_parent)
        self.assertTrue(self.allowed(B))
        self.assertEqual(self.call('get', bootstrap.id).record, bootstrap)
        self.revoke(independent); self.assertFalse(self.allowed(B))

    def test_duplicate_binding_cannot_select_another_parent(self):
        first = self.root(); binding = self.binding(first); second = self.root()
        self.denied(self.bind(second), core.ALREADY_EXISTS)
        self.assertEqual(self.call('bootstrap_get', binding.selector).binding, binding)
        self.assertEqual(self.counts(), (2,1,0))

    def test_binding_revocation_stops_new_grants_without_revoking_existing_grants(self):
        binding = self.binding(); original = self.applied(binding)
        result = self.call('bootstrap_revoke', probe.BootstrapRevoke(caller=A,
            binding_id=binding.id, expected_revision=self.revision))
        self.assertEqual(result.WhichOneof('outcome'), 'binding', result)
        self.revision = result.binding.revoked_revision
        self.assertGreater(self.revision, binding.created_revision)
        self.denied(self.materialize(binding, recipient=C))
        self.assertEqual(self.applied(binding), original)
        self.assertTrue(self.allowed(B)); self.assertFalse(self.allowed(C))
        self.assertEqual(self.counts(), (2,1,1))

    def test_unrelated_actor_cannot_revoke_binding(self):
        binding = self.binding()
        self.denied(self.call('bootstrap_revoke', probe.BootstrapRevoke(caller=C,
            binding_id=binding.id, expected_revision=self.revision)))
        self.assertEqual(self.call('bootstrap_get', binding.selector).binding, binding)

    def test_domain_administration_can_stop_a_binding_without_gaining_its_rights(self):
        binding = self.binding()
        self.root(self.terms(C, rights=(pb.POLICY_ADMINISTER,), depth=0))
        result = self.call('bootstrap_revoke', probe.BootstrapRevoke(caller=C,
            binding_id=binding.id, expected_revision=self.revision))
        self.assertEqual(result.WhichOneof('outcome'), 'binding', result)
        self.revision = result.binding.revoked_revision
        self.denied(self.materialize(binding))
        self.assertFalse(self.allowed(C))

    def test_preparation_collects_all_parent_identities_before_first_grant(self):
        parent = self.root(); child = self.child(parent, self.terms(C,depth=2))
        binding = self.binding(child, caller=C)
        result = self.call('bootstrap_subjects', probe.BootstrapSubjects(binding_ids=[binding.id],recipient=B))
        self.assertEqual(result.WhichOneof('outcome'), 'subjects', result)
        self.assertEqual({a.SerializeToString() for a in result.subjects.actors},
                         {a.SerializeToString() for a in (A,B,C)})
        self.assertEqual(self.counts(), (2,1,0))
        self.denied(self.materialize(binding, inactive=(A,)))

    def test_recipient_must_belong_to_the_selected_issuer(self):
        binding = self.binding()
        wrong = pb.ActorRef(issuer_id=b'x'*32, actor_id=B.actor_id)
        self.denied(self.materialize(binding,recipient=wrong))
        self.assertEqual(self.counts(), (1,1,0))

    def test_missing_receipt_target_is_storage_failure_not_reissuance(self):
        binding = self.binding(); original = self.applied(binding)
        with sqlite3.connect(self.state/'authority.db') as db:
            db.execute('DELETE FROM grant_record WHERE id=?',(original.id,))
        self.denied(self.materialize(binding), core.STORAGE_ERROR)
        self.assertEqual(self.counts(), (1,1,1))

    def test_binding_write_failure_preserves_revision_and_records(self):
        parent = self.root()
        with sqlite3.connect(self.state/'authority.db') as db:
            db.execute("CREATE TRIGGER fail_binding BEFORE INSERT ON bootstrap_binding BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.denied(self.bind(parent),core.STORAGE_ERROR)
        self.assertTrue(self.allowed(A)); self.assertEqual(self.counts(), (1,0,0))

    def test_invalid_selectors_and_recipient_bearing_templates_are_rejected(self):
        parent = self.root()
        invalid=[]
        for field,value in [('issuer_id',b'x'*31),('profile_id',''),('profile_id','x'*129),
                            ('template_id',''),('template_id','bad\0id'),('profile_revision',0),('profile_revision',2**63)]:
            selector=self.selector();setattr(selector,field,value);invalid.append(selector)
        for selector in invalid:
            with self.subTest(selector=selector): self.denied(self.bind(parent,selector=selector),core.INVALID_ARGUMENT)
        self.denied(self.bind(parent,terms=self.terms(B,depth=0)),core.INVALID_ARGUMENT)
        self.assertEqual(self.counts(), (1,0,0))

    def test_explicit_v1_migration_preserves_grants_revocations_and_domain(self):
        parent = self.root(); child = self.child(parent); revoked = self.revoke(child)
        self.stop()
        with sqlite3.connect(self.state/'authority.db') as db:
            before = db.execute('SELECT * FROM grant_record ORDER BY id').fetchall()
            authority = db.execute('SELECT * FROM authority').fetchall()
            db.executescript('DROP TABLE bootstrap_receipt; DROP TABLE bootstrap_binding; PRAGMA user_version=1;')
        command=[str(BINARY),str(self.state),str(self.domain.kind),self.domain.id]
        refused=subprocess.run(command+['open'],input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(refused.returncode,0);self.assertIn(b'migration',refused.stderr.lower())
        for _ in range(2):
            migrated=subprocess.run(self.migrate_command(),input=b'',capture_output=True,timeout=4)
            self.assertEqual(migrated.returncode,0,migrated.stderr)
        with sqlite3.connect(self.state/'authority.db') as db:
            self.assertEqual(db.execute('SELECT * FROM grant_record ORDER BY id').fetchall(),before)
            self.assertEqual(db.execute('SELECT * FROM authority').fetchall(),authority)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
        self.start(); self.assertEqual(self.call('get',revoked.id).record,revoked)
        self.assertFalse(self.allowed(B)); self.assertTrue(self.allowed(A))
        self.binding(parent)

    def test_failed_migration_rolls_back_schema_changes(self):
        self.root(); self.stop()
        with sqlite3.connect(self.state/'authority.db') as db:
            before=db.execute('SELECT * FROM grant_record').fetchall()
            db.executescript('DROP TABLE bootstrap_receipt; DROP TABLE bootstrap_binding; '
                             'CREATE TABLE bootstrap_receipt(marker BLOB); PRAGMA user_version=1;')
        command=self.migrate_command()
        result=subprocess.run(command,input=b'',capture_output=True,timeout=4)
        self.assertNotEqual(result.returncode,0)
        with sqlite3.connect(self.state/'authority.db') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT * FROM grant_record').fetchall(),before)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='bootstrap_binding'").fetchall())

    def test_migration_tool_refuses_live_store_lock(self):
        self.root()
        result = subprocess.run(self.migrate_command(), capture_output=True, timeout=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'locked', result.stderr)
        self.assertTrue(self.allowed(A))

    def test_migration_wrong_domain_preserves_legacy_schema(self):
        self.root(); self.stop()
        with sqlite3.connect(self.state/'authority.db') as db:
            before = db.execute('SELECT * FROM authority').fetchall()
            db.executescript('DROP TABLE bootstrap_receipt; DROP TABLE bootstrap_binding; PRAGMA user_version=1;')
        for kind, identity in [('world', 'other-world'), ('workspace', self.domain.id)]:
            result = subprocess.run(self.migrate_command(kind, identity), capture_output=True, timeout=4)
            self.assertNotEqual(result.returncode, 0)
        with sqlite3.connect(self.state/'authority.db') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT * FROM authority').fetchall(), before)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='bootstrap_binding'").fetchall())

    def test_migration_tool_requires_explicit_valid_arguments(self):
        for args in [[], ['--state-dir', str(self.state)],
                     ['--state-dir', str(self.state), '--domain-kind', 'invalid', '--domain-id', 'world-one']]:
            result = subprocess.run([str(MIGRATE)]+args, capture_output=True, timeout=4)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
        self.assertEqual(self.counts(), (0,0,0))

    def test_workspace_binding_preserves_filesystem_scope_and_parent_revocation(self):
        self.stop()
        self.state = self.directory/'workspace'; self.state.mkdir(mode=0o700)
        self.domain = pb.Domain(kind=pb.WORKSPACE, id='project-one')
        self.start(True)
        parent = self.root(self.terms(rights=(pb.FILE_READ,), depth=1))
        binding = self.binding(parent, terms=self.template(scope=self.scope(b'project'), rights=(pb.FILE_READ,)))
        self.applied(binding)
        self.assertTrue(self.allowed(B, self.scope(b'project'), pb.FILE_READ))
        self.assertFalse(self.allowed(B, self.scope(b'other'), pb.FILE_READ))
        self.assertFalse(self.allowed(B, self.scope(b'project'), pb.FILE_EDIT))
        self.revoke(parent)
        self.assertFalse(self.allowed(B, self.scope(b'project'), pb.FILE_READ))

    def test_binding_limit_preserves_existing_bindings_and_revision(self):
        parent = self.root()
        first = self.binding(parent, selector=self.selector(template='0'))
        for index in range(1,1024):
            self.binding(parent, selector=self.selector(template=str(index)))
        self.denied(self.bind(parent, selector=self.selector(template='overflow')), core.RESOURCE_EXHAUSTED)
        self.assertTrue(self.allowed(A))
        self.assertEqual(self.counts(), (1,1024,0))
        self.applied(first)
        self.assertTrue(self.allowed(B))

    def test_grant_capacity_cannot_be_bypassed_by_materialization(self):
        parent = self.root(); binding = self.binding(parent)
        # Populate valid durable roots directly to reach the existing 4096-record boundary.
        # This fixture setup does not implement grant decisions or materialization.
        with sqlite3.connect(self.state/'authority.db') as db:
            for index in range(1,4096):
                record = pb.GrantRecord(); record.CopyFrom(parent)
                record.id = index.to_bytes(32,'big'); record.created_revision = self.revision+index
                db.execute('INSERT INTO grant_record VALUES(?,?,?,?)',
                    (record.id,A.issuer_id,A.actor_id,record.SerializeToString()))
            self.revision += 4095
            db.execute('UPDATE authority SET revision=?',(self.revision,))
        self.denied(self.materialize(binding), core.RESOURCE_EXHAUSTED)
        self.assertEqual(self.counts(), (4096,1,0))
        self.assertFalse(self.allowed(B))

    def test_corrupt_binding_cannot_materialize_or_disclose_a_valid_binding(self):
        binding = self.binding()
        for corruption in ('selector','authorizer','rights','domain','revision','unknown'):
            bad = pb.BootstrapBinding(); bad.CopyFrom(binding)
            if corruption == 'selector': bad.selector.profile_id='substitute'
            elif corruption == 'authorizer': bad.authorizer.CopyFrom(C)
            elif corruption == 'rights': bad.terms.rights.append(pb.WORLD_OBSERVE)
            elif corruption == 'domain': bad.terms.scope.domain.id='other-world'
            elif corruption == 'revision': bad.revoked_revision=self.revision+1
            elif corruption == 'unknown': bad.ParseFromString(bad.SerializeToString()+b'\xf8\x07\x01')
            with self.subTest(corruption=corruption):
                with sqlite3.connect(self.state/'authority.db') as db:
                    db.execute('UPDATE bootstrap_binding SET record=? WHERE id=?',(bad.SerializeToString(),binding.id))
                self.denied(self.materialize(binding), core.STORAGE_ERROR)
                self.denied(self.call('bootstrap_get',binding.selector), core.STORAGE_ERROR)
                self.assertEqual(self.counts(), (1,1,0))
        with sqlite3.connect(self.state/'authority.db') as db:
            db.execute('UPDATE bootstrap_binding SET record=? WHERE id=?',(binding.SerializeToString(),binding.id))
        self.applied(binding)

    def test_missing_binding_parent_is_storage_failure(self):
        binding = self.binding()
        with sqlite3.connect(self.state/'authority.db') as db:
            db.execute('DELETE FROM grant_record WHERE id=?',(binding.parent_id,))
        self.denied(self.materialize(binding), core.STORAGE_ERROR)
        self.assertEqual(self.counts(), (0,1,0))


if __name__ == '__main__': unittest.main(verbosity=2)
