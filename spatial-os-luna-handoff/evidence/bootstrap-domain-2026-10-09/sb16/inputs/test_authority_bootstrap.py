"""DRAFT: production domain binding and receipt counterexamples.

Move into tests/core only with the additive probe schema and fixture extraction.
Its baseline can run against the existing store with isolated proposed bindings.
It does not establish domain grant delivery.
"""
import sqlite3
import unittest

import core_pb2 as core
import grants_pb2 as pb
import grants_probe_pb2 as probe
from test_authority import AuthorityFixture, A, B, C


class BootstrapAuthorityTests(AuthorityFixture):
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


if __name__ == '__main__': unittest.main(verbosity=2)
