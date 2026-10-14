"""Stopped owner-authorized additions preserve participant identity and exact receipts."""
import hashlib
import sqlite3
import subprocess
import sys
from issuer_migration_runner import run_issuer_migration
import unittest
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as pb
import test_authority_fencing as fencing
from test_identity_issuer import BINARY


class ParticipantDomainAdditionTests(fencing.AuthorityFencingTests):
    def setUp(self):
        super().setUp()
        self.stopped = False

    def stop_initial_generation(self):
        if self.stopped:
            return
        reply = self.peer.call(authority_release=pb.AuthorityRelease(generation=1, applied_revision=0))
        self.assertTrue(reply.HasField('authority_activation'), reply)
        self.stopped = True

    def addition(self, opid=b'd'*16, additions=None, **changes):
        value = pb.AuthorityParticipantDomainsAdd(operation_id=opid,
            participant_id=self.registration.participant.id,
            expected_public_key=self.registration.participant.public_key,
            expected_registered_revision=1, expected_registry_revision=1,
            expected_supervision_revision=1,
            additions=[grants.Domain(kind=grants.WORKSPACE, id='workspace-copy')] if additions is None else additions)
        for name, item in changes.items():
            setattr(value, name, item)
        return value

    def add_domains(self, request, key=None, public=None):
        operation = pb.IssuerOperation(participant_domains_add=request)
        challenge = self.challenge(self.peer, operation)
        return self.execute(self.peer, challenge,
            self.proof(challenge, key or self.owner, public or self.owner_public))

    def test_stopped_addition_preserves_identity_and_returns_exact_supervision_receipt(self):
        self.stop_initial_generation()
        before = self.snapshot_all()
        request = self.addition()
        reply = self.add_domains(request)
        self.assertTrue(reply.HasField('participant_domains_added'), reply)
        receipt = reply.participant_domains_added
        self.assertEqual(receipt.operation_id, request.operation_id)
        self.assertEqual(receipt.request_bytes, request.SerializeToString())
        self.assertEqual(receipt.request_sha256, hashlib.sha256(receipt.request_bytes).digest())
        self.assertEqual(receipt.old_registration, self.registration)
        self.assertEqual(receipt.registry_revision_before, 1)
        self.assertEqual(receipt.registry_revision_after, 2)
        self.assertEqual(receipt.new_registration.registered_revision, 2)
        self.assertEqual(receipt.new_registration.participant.id, self.registration.participant.id)
        self.assertEqual(receipt.new_registration.participant.public_key, self.registration.participant.public_key)
        self.assertEqual([(d.kind, d.id) for d in receipt.new_registration.participant.domains],
            [(grants.WORLD, 'park'), (grants.WORKSPACE, 'workspace-copy')])
        self.assertEqual(receipt.old_supervision, self.good_binding())
        self.assertEqual(receipt.new_supervision.registration, receipt.new_registration)
        self.assertEqual(receipt.new_supervision.public_key, self.supervisor_public)
        self.assertEqual(receipt.new_supervision.revision, 2)
        after = self.snapshot_all()
        for name in before:
            if name not in ('authority_participant', 'authority_participant_domain', 'authority_registry',
                            'authority_participant_history', 'authority_supervisor', 'authority_domains_add_operation'):
                self.assertEqual(after[name], before[name], name)
        self.assertEqual(len(after['authority_domains_add_operation']), 1)
        replay = self.add_domains(request)
        self.assertEqual(replay.participant_domains_added.SerializeToString(), receipt.SerializeToString())
        second = self.addition(opid=b'e'*16, additions=[grants.Domain(kind=grants.WORLD, id='new-world')],
            expected_registered_revision=2, expected_registry_revision=2, expected_supervision_revision=2)
        self.assertTrue(self.add_domains(second).HasField('participant_domains_added'))
        self.restart()
        self.assertEqual(self.add_domains(request).participant_domains_added.SerializeToString(), receipt.SerializeToString())
        changed = self.addition(opid=request.operation_id, additions=[grants.Domain(kind=grants.WORLD, id='changed')])
        conflict = self.add_domains(changed)
        self.assertTrue(conflict.HasField('error'), conflict)
        self.assertEqual(conflict.error.code, core.OPERATION_CONFLICT)

    def test_empty_missing_foreign_and_over_limit_domain_requests_are_atomic(self):
        self.stop_initial_generation()
        before = self.snapshot_all()
        empty = self.addition(opid=b'm'*16, additions=[])
        reply = self.peer.call(challenge=pb.IssuerOperation(participant_domains_add=empty))
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.INVALID_ARGUMENT)
        self.assertEqual(self.snapshot_all(), before)
        full = self.addition(opid=b'x'*16)
        for field in ('expected_registered_revision', 'expected_registry_revision', 'expected_supervision_revision'):
            missing = pb.AuthorityParticipantDomainsAdd()
            missing.CopyFrom(full)
            missing.ClearField(field)
            reply = self.peer.call(challenge=pb.IssuerOperation(participant_domains_add=missing))
            self.assertTrue(reply.HasField('error'), (field, reply))
            self.assertEqual(reply.error.code, core.INVALID_ARGUMENT, field)
            self.assertEqual(self.snapshot_all(), before, field)
        other_key, other_public = self.key('foreign-domain-owner')
        self.register(identity=b'c'*32, public=other_public, domain='claimed-world', revision=1)
        before_denials = self.snapshot_all()
        foreign = self.addition(opid=b'n'*16, expected_registry_revision=2,
            additions=[grants.Domain(kind=grants.WORLD, id='claimed-world')])
        reply = self.add_domains(foreign)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.IN_USE)
        self.assertEqual(self.snapshot_all(), before_denials)
        over = self.addition(opid=b'o'*16, expected_registry_revision=2,
            additions=[grants.Domain(kind=grants.WORKSPACE, id=f'overflow-{i:03d}') for i in range(64)])
        reply = self.add_domains(over)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.RESOURCE_EXHAUSTED)
        self.assertEqual(self.snapshot_all(), before_denials)

    def test_receipt_saturation_rejects_without_evicting_accepted_receipts(self):
        self.stop_initial_generation()
        saved_request = None
        saved_receipt_bytes = None
        first_current_registration = None
        first_current_supervision_revision = None
        current_registry = 1
        participants = [(self.registration, 1)]
        for index in range(1, 64):
            _, public = self.key(f'receipt-fill-{index}')
            record = self.register(identity=bytes([index])*32, public=public,
                domain=f'receipt-world-{index:02d}', revision=current_registry)
            current_registry += 1
            participants.append((record, current_registry))
        for index, (record, revision) in enumerate(participants):
            participant = record.participant
            request = pb.AuthorityParticipantDomainsAdd(operation_id=(index+1).to_bytes(16, 'big'),
                participant_id=participant.id, expected_public_key=participant.public_key,
                expected_registered_revision=record.registered_revision,
                expected_registry_revision=current_registry, expected_supervision_revision=(1 if index == 0 else 0),
                additions=[grants.Domain(kind=grants.WORKSPACE, id=f'receipt-copy-{index:02d}')])
            operation = pb.IssuerOperation(participant_domains_add=request)
            challenge = self.challenge(self.peer, operation)
            reply = self.execute(self.peer, challenge)
            self.assertTrue(reply.HasField('participant_domains_added'), reply)
            if index == 0:
                saved_request = request
                saved_receipt_bytes = reply.participant_domains_added.SerializeToString()
                first_current_registration = reply.participant_domains_added.new_registration.registered_revision
                first_current_supervision_revision = reply.participant_domains_added.new_supervision.revision
            current_registry += 1
        with sqlite3.connect(self.state/'identity.db') as db:
            count = db.execute('SELECT count(*) FROM authority_domains_add_operation').fetchone()[0]
            head = db.execute('SELECT revision FROM authority_registry').fetchone()[0]
        self.assertEqual(count, 64)
        self.assertEqual(head, current_registry)
        before = self.snapshot_all()
        full = pb.AuthorityParticipantDomainsAdd(operation_id=b'z'*16,
            participant_id=self.registration.participant.id,
            expected_public_key=self.registration.participant.public_key,
            expected_registered_revision=first_current_registration, expected_registry_revision=current_registry,
            expected_supervision_revision=first_current_supervision_revision,
            additions=[grants.Domain(kind=grants.WORKSPACE, id='receipt-capacity-overflow')])
        denied = self.add_domains(full)
        self.assertTrue(denied.HasField('error'), denied)
        self.assertEqual(denied.error.code, core.RESOURCE_EXHAUSTED)
        self.assertEqual(self.snapshot_all(), before)
        replay = self.add_domains(saved_request)
        self.assertTrue(replay.HasField('participant_domains_added'), replay)
        self.assertEqual(replay.participant_domains_added.SerializeToString(), saved_receipt_bytes)
        self.assertEqual(self.snapshot_all(), before)

    def test_addition_uses_global_registry_revision_after_interleaved_registration(self):
        self.stop_initial_generation()
        other_key, other_public = self.key('interleaved-participant')
        other = self.register(identity=b'b'*32, public=other_public, domain='other-world', revision=1)
        request = self.addition(opid=b'k'*16, expected_registered_revision=1,
                                expected_registry_revision=2, expected_supervision_revision=1)
        reply = self.add_domains(request)
        self.assertTrue(reply.HasField('participant_domains_added'), reply)
        receipt = reply.participant_domains_added
        self.assertEqual(receipt.new_registration.registered_revision, 3)
        self.assertEqual(receipt.new_supervision.revision, 2)
        with sqlite3.connect(self.state/'identity.db') as db:
            current = dict(db.execute('SELECT id,registered_revision FROM authority_participant'))
            head = db.execute('SELECT revision FROM authority_registry').fetchone()[0]
        self.assertEqual(head, 3)
        self.assertEqual(current[self.registration.participant.id], 3)
        self.assertEqual(current[other.participant.id], 2)
        self.assertEqual(len(set(current.values())), len(current))

    def test_invalid_or_stale_additions_leave_registry_unchanged(self):
        self.stop_initial_generation()
        before = self.snapshot_all()
        cases = [
            (self.addition(expected_registered_revision=2), core.REVISION_CONFLICT),
            (self.addition(expected_registry_revision=2), core.REVISION_CONFLICT),
            (self.addition(expected_public_key=b'x'*32), core.REVISION_CONFLICT),
            (self.addition(additions=[grants.Domain(kind=grants.WORLD, id='park')]), core.ALREADY_EXISTS),
        ]
        for request, code in cases:
            with self.subTest(request=request):
                reply = self.add_domains(request)
                self.assertTrue(reply.HasField('error'), reply)
                self.assertEqual(reply.error.code, code)
                self.assertEqual(self.snapshot_all(), before)
        duplicate = self.addition(additions=[grants.Domain(kind=grants.WORKSPACE, id='z'),
                                            grants.Domain(kind=grants.WORKSPACE, id='z')])
        malformed = self.peer.call(challenge=pb.IssuerOperation(participant_domains_add=duplicate))
        self.assertTrue(malformed.HasField('error'), malformed)
        self.assertEqual(malformed.error.code, core.INVALID_ARGUMENT)
        self.assertEqual(malformed.error.message, 'Domain additions must be unique and canonically sorted')
        self.assertEqual(self.snapshot_all(), before)
        wrong_key = self.addition(opid=b'f'*16)
        reply = self.add_domains(wrong_key, key=self.signer, public=self.public)
        self.assertTrue(reply.HasField('error'), reply)
        self.assertEqual(reply.error.code, core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before)

    def test_old_fence_retirement_and_barrier_remain_readable_after_addition(self):
        self.revoke_actor_for_test()
        fence = self.fenced()
        retirement_request = pb.AuthorityRetireBegin(owner=self.fence_request(), fence_id=fence.fence_id,
            cleanup_sha256=b'd'*32)
        # Controlled storage-fault injection isolates the defensive obligation guard.
        # Valid release requires applied_revision == head, so normal API state cannot reach this branch.
        with sqlite3.connect(self.state/'identity.db') as db:
            changed = db.execute('UPDATE authority_activation SET active=0 WHERE participant_id=? AND generation=1',
                                 (self.registration.participant.id,)).rowcount
        self.assertEqual(changed, 1)
        before_busy = self.snapshot_all()
        busy = self.add_domains(self.addition(opid=b'i'*16))
        self.assertTrue(busy.HasField('error'), busy)
        self.assertEqual(busy.error.code, core.BUSY)
        self.assertEqual(self.snapshot_all(), before_busy)
        with sqlite3.connect(self.state/'identity.db') as db:
            changed = db.execute('UPDATE authority_activation SET active=1 WHERE participant_id=? AND generation=1',
                                 (self.registration.participant.id,)).rowcount
        self.assertEqual(changed, 1)
        challenge = self.supervisor.call(authority_retire_begin=retirement_request)
        self.assertTrue(challenge.HasField('challenge'), challenge)
        retired = self.supervisor.call(authority_retire_complete=pb.AuthorityRetireComplete(
            nonce=challenge.challenge.nonce,
            proof=self.proof(challenge.challenge, self.supervisor_key, self.supervisor_public)))
        self.assertTrue(retired.HasField('authority_retirement'), retired)
        retirement_state = retired.authority_retirement
        added = self.add_domains(self.addition(opid=b'j'*16))
        self.assertTrue(added.HasField('participant_domains_added'), added)
        before_retry = self.snapshot_all()
        expanded_scope = self.fence_request(registered_revision=2, supervision_revision=2)
        rejected = self.supervisor.call(authority_fence_begin=expanded_scope)
        self.assertTrue(rejected.HasField('error'), rejected)
        self.assertEqual(rejected.error.code, core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), before_retry)
        self.assertEqual(self.fenced().SerializeToString(), fence.SerializeToString())
        retry = self.supervisor.call(authority_retire_begin=retirement_request)
        self.assertTrue(retry.HasField('challenge'), retry)
        repeated = self.supervisor.call(authority_retire_complete=pb.AuthorityRetireComplete(
            nonce=retry.challenge.nonce,
            proof=self.proof(retry.challenge, self.supervisor_key, self.supervisor_public)))
        self.assertTrue(repeated.HasField('authority_retirement'), repeated)
        self.assertEqual(repeated.authority_retirement.SerializeToString(), retirement_state.SerializeToString())
        barrier = self.barrier_for_test(1)
        self.assertTrue(barrier.complete)
        self.assertEqual(barrier.obligations[0].registration.registered_revision, 1)
        self.assertEqual([d.id for d in barrier.obligations[0].registration.participant.domains], ['park'])
        self.assertEqual(barrier.obligations[0].retirement.SerializeToString(), retirement_state.SerializeToString())

    def test_active_generation_and_unconsumed_launch_are_busy(self):
        self.stop_initial_generation()
        active_peer = self.connect()
        challenge = self.service_begin(active_peer, self.request(revision=1, incarnation=b'a'*32))
        authenticated = self.service_finish(challenge, active_peer)
        self.assertTrue(authenticated.HasField('authority_authentication'), authenticated)
        service = authenticated.authority_authentication
        request = self.launch_request(service=service, launch_id=b'b'*32, previous=1, revision=1)
        authorized = self.launch_complete(self.launch_begin(request))
        self.assertTrue(authorized.HasField('authority_launch_authorization'), authorized)
        busy = self.add_domains(self.addition(opid=b'g'*16))
        self.assertTrue(busy.HasField('error'), busy)
        self.assertEqual(busy.error.code, core.BUSY)
        self.assertTrue(self.activate(active_peer).HasField('authority_activation'))
        busy = self.add_domains(self.addition(opid=b'h'*16))
        self.assertTrue(busy.HasField('error'), busy)
        self.assertEqual(busy.error.code, core.BUSY)

    def test_explicit_v10_migration_is_atomic_and_preserves_old_rows(self):
        self.stop_initial_generation()
        self.peer.close(); self.supervisor.close(); self.stop_daemons()
        secret = (self.state/'issuer.pem').read_bytes()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.executescript('DROP TABLE authority_domains_add_operation; DROP TABLE authority_participant_history; PRAGMA user_version=10;')
        before = self.snapshot_all()
        command = [str(BINARY), '--state-dir', str(self.state)]
        ordinary = run_issuer_migration(command, input=b'', capture_output=True, timeout=4)
        self.assertNotEqual(ordinary.returncode, 0)
        self.assertEqual(self.snapshot_all(), before)
        for _ in range(2):
            migrated = run_issuer_migration(command+['--migrate'], input=b'', capture_output=True, timeout=4)
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
        after = self.snapshot_all()
        self.assertEqual({name: after[name] for name in before}, before)
        with sqlite3.connect(self.state/'identity.db') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 11)
        self.assertEqual((self.state/'issuer.pem').read_bytes(), secret)
        self.peer = self.ready(); self.supervisor = self.client()

    def test_malformed_v10_history_migration_rolls_back(self):
        self.stop_initial_generation()
        self.peer.close(); self.supervisor.close(); self.stop_daemons()
        with sqlite3.connect(self.state/'identity.db') as db:
            db.execute('UPDATE authority_activation SET registration=?', (b'\x00',))
            db.executescript('DROP TABLE authority_domains_add_operation; DROP TABLE authority_participant_history; PRAGMA user_version=10;')
        before = self.snapshot_all()
        migrated = run_issuer_migration([str(BINARY), '--state-dir', str(self.state), '--migrate'],
            input=b'', capture_output=True, timeout=4)
        self.assertNotEqual(migrated.returncode, 0)
        self.assertEqual(self.snapshot_all(), before)

    def test_cached_service_and_pending_old_revision_proof_are_rejected(self):
        self.stop_initial_generation()
        pending_peer = self.connect()
        pending = self.service_begin(pending_peer, self.request(revision=1, incarnation=b'q'*32))
        reply = self.add_domains(self.addition())
        self.assertTrue(reply.HasField('participant_domains_added'), reply)
        stale_state = self.snapshot_all()
        stale = self.peer.call(authority_state=pb.AuthorityStateRead())
        self.assertTrue(stale.HasField('error'), stale)
        self.assertEqual(stale.error.code, core.UNAUTHORIZED)
        self.assertEqual(stale.error.message, 'Authenticated service registration is stale')
        for stale_call in (
            lambda: self.peer.call(authority_activate=pb.AuthorityActivate(synchronized_revision=0)),
            lambda: self.peer.call(authority_applied=pb.AuthorityApplied(generation=1, from_revision=0, through_revision=1)),
            lambda: self.peer.call(authority_release=pb.AuthorityRelease(generation=1, applied_revision=0)),
        ):
            stale_call = stale_call()
            self.assertTrue(stale_call.HasField('error'), stale_call)
            self.assertEqual(stale_call.error.code, core.UNAUTHORIZED)
            self.assertEqual(stale_call.error.message, 'Authenticated service registration is stale')
            self.assertEqual(self.snapshot_all(), stale_state)
        stale_complete = self.service_finish(pending, pending_peer)
        self.assertTrue(stale_complete.HasField('error'), stale_complete)
        self.assertEqual(stale_complete.error.code, core.UNAUTHORIZED)
        self.assertEqual(self.snapshot_all(), stale_state)
        fresh_peer = self.connect()
        fresh = self.service_begin(fresh_peer, self.request(revision=2, incarnation=b'r'*32))
        authenticated = self.service_finish(fresh, fresh_peer)
        self.assertTrue(authenticated.HasField('authority_authentication'), authenticated)
        self.service = authenticated.authority_authentication
        auth_request = self.launch_request(service=self.service, launch_id=b'n'*32, previous=1, revision=2)
        authorized = self.launch_complete(self.launch_begin(auth_request), peer=self.supervisor)
        self.assertTrue(authorized.HasField('authority_launch_authorization'), authorized)
        active = fresh_peer.call(authority_activate=pb.AuthorityActivate(synchronized_revision=0))
        self.assertTrue(active.HasField('authority_activation'), active)
        self.assertEqual(active.authority_activation.registration.registered_revision, 2)


def load_tests(loader, tests, pattern):
    names = ('test_stopped_addition_preserves_identity_and_returns_exact_supervision_receipt',
             'test_invalid_or_stale_additions_leave_registry_unchanged',
             'test_addition_uses_global_registry_revision_after_interleaved_registration',
             'test_empty_missing_foreign_and_over_limit_domain_requests_are_atomic',
             'test_receipt_saturation_rejects_without_evicting_accepted_receipts',
             'test_active_generation_and_unconsumed_launch_are_busy',
             'test_old_fence_retirement_and_barrier_remain_readable_after_addition',
             'test_explicit_v10_migration_is_atomic_and_preserves_old_rows',
             'test_malformed_v10_history_migration_rolls_back',
             'test_cached_service_and_pending_old_revision_proof_are_rejected')
    return unittest.TestSuite(ParticipantDomainAdditionTests(name) for name in names)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
