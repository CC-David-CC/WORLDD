"""Real issuer profile publication, enrollment, signatures, and storage failures."""
import sqlite3
import subprocess
import unittest

import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
from test_identity_enrollment import EnrollmentFixture, BINARY


class BootstrapTests(EnrollmentFixture):
    def setUp(self):
        super().setUp()
        self.info = self.init()
        self.client = self.start()

    def profile(self, name='starter', rights=(grants.WORLD_ENTER, grants.WORLD_NAVIGATE)):
        return pb.BootstrapProfile(id=name, grants=[pb.BootstrapGrantTemplate(id='world-entry',
            terms=grants.GrantTerms(scope=grants.Scope(domain=grants.Domain(kind=grants.WORLD, id='public-park')),
                                    rights=rights))])

    def publish(self, profile=None, revision=0):
        operation = pb.IssuerOperation(profile_put=pb.BootstrapProfilePut(
            profile=profile or self.profile(), expected_revision=revision))
        reply = self.execute(self.client, self.challenge(self.client, operation))
        self.assertTrue(reply.HasField('profile'), reply)
        return reply.profile

    def read(self, name='starter', revision=1):
        reply = self.client.call(profile_read=pb.BootstrapProfileRead(id=name, revision=revision))
        self.assertTrue(reply.HasField('profile'), reply)
        self.assertEqual(reply.profile.profile.id, name)
        self.assertEqual(reply.profile.revision, revision)
        self.assertEqual(reply.profile.issuer_id, self.info.issuer_id)
        return reply.profile

    def select(self, name='starter', mode=pb.MANUAL):
        before = self.policy(self.client)
        reply = self.execute(self.client, self.challenge(self.client, self.operation(mode, before.revision, name)))
        self.assertTrue(reply.HasField('policy'), reply)
        return reply.policy.revision

    def test_builtin_empty_profile_is_readable_and_immutable(self):
        state = self.read('empty-v1')
        self.assertEqual(len(state.profile.grants), 0)
        request = pb.IssuerOperation(profile_put=pb.BootstrapProfilePut(
            profile=pb.BootstrapProfile(id='empty-v1'), expected_revision=1))
        self.denied(self.execute(self.client, self.challenge(self.client, request)), core.FORBIDDEN)
        self.assertEqual(self.read('empty-v1'), state)

    def test_owner_publishes_world_and_workspace_templates_without_subjects(self):
        profile = self.profile()
        profile.grants.add(id='project-read', terms=grants.GrantTerms(
            scope=grants.Scope(domain=grants.Domain(kind=grants.WORKSPACE, id='project')),
            rights=[grants.WORKSPACE_ACCESS, grants.FILE_READ]))
        state = self.publish(profile)
        self.assertEqual(state.revision, 1)
        self.assertEqual(state.profile, profile)
        self.assertEqual(self.read(), state)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.policy(self.client).revision, 1)

    def test_profile_publication_requires_owner_proof_and_consumes_nonce(self):
        operation = pb.IssuerOperation(profile_put=pb.BootstrapProfilePut(profile=self.profile(), expected_revision=0))
        challenge = self.challenge(self.client, operation)
        self.denied(self.execute(self.client, challenge, self.proof(challenge, self.actor_key, self.actor_public)))
        self.denied(self.execute(self.client, challenge))
        self.denied(self.client.call(profile_read=pb.BootstrapProfileRead(id='starter', revision=1)), core.NOT_FOUND)
        self.assertEqual(self.publish().revision, 1)

    def test_profile_revisions_are_immutable_and_publication_uses_compare_and_swap(self):
        first = self.publish()
        changed = self.profile(rights=(grants.WORLD_ENTER,))
        second = self.publish(changed, 1)
        self.assertEqual(second.revision, 2)
        stale = pb.IssuerOperation(profile_put=pb.BootstrapProfilePut(profile=self.profile(), expected_revision=1))
        self.denied(self.execute(self.client, self.challenge(self.client, stale)), core.REVISION_CONFLICT)
        self.assertEqual(self.read(), first)
        self.assertEqual(self.read(revision=2), second)
        latest = pb.IssuerOperation(profile_read=pb.BootstrapProfileRead(id='starter'))
        self.assertEqual(self.execute(self.client, self.challenge(self.client, latest)).profile, second)

    def test_manual_approval_pins_the_profile_in_an_independently_verified_credential(self):
        self.publish(); revision = self.select()
        pending = self.enroll(self.client)
        approved = self.decide(self.client, pending, revision=revision).enrollment
        claims = self.claims(approved, self.info, 'starter', 1)
        self.assertEqual(claims.policy_revision, revision)
        self.assertTrue(self.client.call(credential_check=approved.credential).identity_status.active)

    def test_automatic_enrollment_uses_the_same_nonempty_profile(self):
        self.publish(); revision = self.select(mode=pb.APPROVE_ALWAYS)
        approved = self.enroll(self.client)
        claims = self.claims(approved, self.info, 'starter', 1)
        self.assertEqual(claims.policy_revision, revision)
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.enroll(self.client), approved)

    def test_active_profile_update_invalidates_pending_manual_approval(self):
        self.publish(); revision = self.select(); pending = self.enroll(self.client)
        operation = pb.IssuerOperation(enrollment_decision=pb.EnrollmentDecision(
            enrollment_id=pending.enrollment_id, choice=pb.APPROVE_NEW, expected_policy_revision=revision))
        challenge = self.challenge(self.client, operation)
        self.publish(self.profile(rights=(grants.WORLD_ENTER,)), 1)
        self.assertEqual(self.policy(self.client).revision, revision+1)
        self.denied(self.execute(self.client, challenge), core.REVISION_CONFLICT)
        self.assertEqual(self.counts(), (1, 0, 0))
        approved = self.decide(self.client, pending, revision=revision+1).enrollment
        self.claims(approved, self.info, 'starter', 2)

    def test_later_profile_updates_do_not_rewrite_recovered_credentials(self):
        first = self.publish(); self.select(mode=pb.APPROVE_ALWAYS)
        approved = self.enroll(self.client)
        second = self.publish(self.profile(rights=(grants.WORLD_ENTER,)), 1)
        self.client.close(crash=True); self.client = self.start()
        self.assertEqual(self.enroll(self.client), approved)
        self.assertEqual(self.read(), first); self.assertEqual(self.read(revision=2), second)
        self.claims(approved, self.info, 'starter', 1)

    def test_automatic_enrollment_never_issues_administration_and_leaves_no_partial_state(self):
        self.publish(self.profile(rights=(grants.POLICY_ADMINISTER,)))
        self.select(mode=pb.APPROVE_ALWAYS)
        self.denied(self.finish(self.client, self.begin(self.client)), core.FORBIDDEN)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_manual_administration_template_still_requires_explicit_owner_approval(self):
        self.publish(self.profile(rights=(grants.POLICY_ADMINISTER,)))
        revision = self.select(); pending = self.enroll(self.client)
        self.assertEqual(pending.status, pb.PENDING)
        approved = self.decide(self.client, pending, revision=revision).enrollment
        self.claims(approved, self.info, 'starter', 1)
        # A signed template is not a domain grant or issuer administration credential.
        challenge = self.challenge(self.client)
        self.denied(self.execute(self.client, challenge, self.proof(challenge, self.actor_key, self.actor_public)))

    def test_malformed_templates_are_rejected_before_publication(self):
        bad = [pb.BootstrapProfile(), pb.BootstrapProfile(id='x'*129), pb.BootstrapProfile(id='x\0y')]
        for alteration in ['duplicate', 'subject', 'scope', 'world-right', 'duplicate-right', 'delegable', 'depth', 'zero-depth', 'template-id', 'too-many']:
            profile = self.profile(); template = profile.grants[0]
            if alteration == 'duplicate': profile.grants.add().CopyFrom(template)
            elif alteration == 'subject': template.terms.subject.CopyFrom(grants.ActorRef(issuer_id=b'i'*32, actor_id=b'a'*32))
            elif alteration == 'scope': template.terms.ClearField('scope')
            elif alteration == 'world-right': template.terms.rights.append(grants.FILE_READ)
            elif alteration == 'duplicate-right': template.terms.rights.append(grants.WORLD_ENTER)
            elif alteration == 'delegable': template.terms.delegable_rights.append(grants.WORLD_OBSERVE); template.terms.delegation_depth=1
            elif alteration == 'depth': template.terms.delegation_depth=33
            elif alteration == 'zero-depth': template.terms.delegable_rights.append(grants.WORLD_ENTER)
            elif alteration == 'template-id': template.id=''
            elif alteration == 'too-many':
                for i in range(32): profile.grants.add(id=str(i), terms=template.terms)
            bad.append(profile)
        for index, profile in enumerate(bad):
            with self.subTest(index=index):
                reply = self.client.call(challenge=pb.IssuerOperation(profile_put=pb.BootstrapProfilePut(profile=profile, expected_revision=0)))
                self.denied(reply, core.INVALID_ARGUMENT)
        self.assertEqual(self.counts(), (0, 0, 0)); self.assertEqual(self.policy(self.client).revision, 1)

    def test_profile_size_and_unknown_fields_are_rejected(self):
        oversized = self.profile()
        for i in range(5):
            template = oversized.grants.add(id=str(i), terms=oversized.grants[0].terms)
            template.terms.scope.components.extend([b'x'*256]*32)
        self.assertGreater(oversized.ByteSize(), 32768)
        unknown = self.profile(); unknown.ParseFromString(unknown.SerializeToString()+b'\xf8\x07\x01')
        for profile in [oversized, unknown]:
            self.denied(self.client.call(challenge=pb.IssuerOperation(profile_put=pb.BootstrapProfilePut(profile=profile, expected_revision=0))), core.INVALID_ARGUMENT)

    def test_service_reads_require_exact_valid_revisions(self):
        for request in [pb.BootstrapProfileRead(id='empty-v1'), pb.BootstrapProfileRead(id='empty-v1', revision=0),
                        pb.BootstrapProfileRead(id='empty-v1', revision=2**63), pb.BootstrapProfileRead(id='', revision=1)]:
            self.denied(self.client.call(profile_read=request), core.INVALID_ARGUMENT)
        self.denied(self.client.call(profile_read=pb.BootstrapProfileRead(id='missing', revision=1)), core.NOT_FOUND)
        self.denied(self.client.call(profile_read=pb.BootstrapProfileRead(id='empty-v1', revision=2)), core.NOT_FOUND)

    def test_failed_profile_publication_preserves_history_and_active_policy_revision(self):
        first = self.publish(); revision = self.select()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER fail_profile BEFORE INSERT ON bootstrap_profile BEGIN SELECT RAISE(ABORT, 'injected'); END")
        operation = pb.IssuerOperation(profile_put=pb.BootstrapProfilePut(profile=self.profile(), expected_revision=1))
        self.denied(self.execute(self.client, self.challenge(self.client, operation)), core.STORAGE_ERROR)
        self.assertEqual(self.read(), first); self.assertEqual(self.policy(self.client).revision, revision)
        self.denied(self.client.call(profile_read=pb.BootstrapProfileRead(id='starter', revision=2)), core.NOT_FOUND)

    def test_failed_credential_write_rolls_back_nonempty_profile_issuance(self):
        self.publish(); revision = self.select(); pending = self.enroll(self.client)
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute("CREATE TRIGGER fail_credential BEFORE INSERT ON credential BEGIN SELECT RAISE(ABORT, 'injected'); END")
        self.denied(self.decide(self.client, pending, revision=revision), core.STORAGE_ERROR)
        self.assertEqual(self.counts(), (1, 0, 0))
        self.assertEqual(self.inspect(self.client, pending).enrollment.status, pb.PENDING)
        with sqlite3.connect(self.state/'identity.db') as db: db.execute('DROP TRIGGER fail_credential')
        self.claims(self.decide(self.client, pending, revision=revision).enrollment, self.info, 'starter', 1)

    def test_corrupt_profile_blocks_reads_and_issuance(self):
        self.publish(); self.select(mode=pb.APPROVE_ALWAYS)
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute('UPDATE bootstrap_profile SET profile=? WHERE id=?', (self.profile(name='substituted').SerializeToString(), b'starter'))
        self.denied(self.client.call(profile_read=pb.BootstrapProfileRead(id='starter', revision=1)), core.STORAGE_ERROR)
        self.denied(self.finish(self.client, self.begin(self.client)), core.STORAGE_ERROR)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_second_credential_keeps_actor_identity_and_pins_its_explicit_profile(self):
        first = self.decide(self.client, self.enroll(self.client)).enrollment
        old_claims = self.claims(first, self.info)
        self.publish(); revision = self.select()
        key, public = self.key('second')
        pending = self.enroll(self.client, self.intent(public), key, public)
        second = self.decide(self.client, pending, choice=pb.APPROVE_EXISTING, target=old_claims.actor_id, revision=revision).enrollment
        claims = self.claims(second, self.info, 'starter', 1)
        self.assertEqual(claims.actor_id, old_claims.actor_id)
        self.assertNotEqual(claims.credential_id, old_claims.credential_id)
        self.assertEqual(self.counts(), (2, 1, 2))

    def test_explicit_v3_migration_preserves_credentials_and_is_repeatable(self):
        approved = self.decide(self.client, self.enroll(self.client)).enrollment
        self.client.close(); secret = (self.state/'issuer.pem').read_bytes(); policy = self.snapshot()
        with sqlite3.connect(self.state/'identity.db') as db:
            before = db.execute('SELECT * FROM credential').fetchall()
            db.executescript('DROP TABLE IF EXISTS bootstrap_profile; PRAGMA user_version=3;')
        command = [str(BINARY), '--state-dir', str(self.state)]
        old = subprocess.run(command, input=b'', capture_output=True, timeout=4)
        self.assertNotEqual(old.returncode, 0); self.assertIn(b'migration', old.stderr.lower())
        for _ in range(2):
            migrated = subprocess.run(command+['--migrate'], input=b'', capture_output=True, timeout=4)
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
        with sqlite3.connect(self.state/'identity.db') as db:
            self.assertEqual(db.execute('SELECT * FROM credential').fetchall(), before)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
        self.assertEqual((self.state/'issuer.pem').read_bytes(), secret); self.assertEqual(self.snapshot(), policy)
        self.client = self.start(); self.assertEqual(self.enroll(self.client), approved)
        self.assertEqual(len(self.read('empty-v1').profile.grants), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
