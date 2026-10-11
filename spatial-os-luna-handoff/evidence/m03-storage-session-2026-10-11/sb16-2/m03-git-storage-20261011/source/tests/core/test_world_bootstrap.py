"""Draft: real issuer proofs and explicit domain bindings control bootstrap delivery."""
import unittest

import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
import world_session_pb2 as session
import test_world_authority as existing
from test_world_identity import WorldIdentityFixture, WorldProcess


class BootstrapWorldFixture(WorldIdentityFixture):
    principal = existing.WorldAuthorityTests.principal
    terms = existing.WorldAuthorityTests.terms
    scoped_world = existing.WorldAuthorityTests.scoped_world
    own = existing.WorldAuthorityTests.own
    root_grant = existing.WorldAuthorityTests.root_grant
    revoke_grant = existing.WorldAuthorityTests.revoke_grant
    navigation = existing.WorldAuthorityTests.navigation
    delegate = existing.WorldAuthorityTests.delegate

    def publish(self, rights=(grant.WORLD_ENTER,grant.WORLD_NAVIGATE), revision=0, name='starter', domain_id=None):
        terms=self.terms(rights=rights,depth=0);terms.ClearField('subject')
        if domain_id is not None: terms.scope.domain.id=domain_id
        profile=issuer.BootstrapProfile(id=name,grants=[issuer.BootstrapGrantTemplate(id='entry',terms=terms)])
        operation=issuer.IssuerOperation(profile_put=issuer.BootstrapProfilePut(profile=profile,expected_revision=revision))
        reply=self.execute(self.owner_peer,self.challenge(self.owner_peer,operation))
        self.assertTrue(reply.HasField('profile'),reply)
        return reply.profile

    def select_profile(self, name='starter', mode=issuer.APPROVE_ALWAYS):
        current=self.policy(self.owner_peer)
        result=self.execute(self.owner_peer,self.challenge(self.owner_peer,self.operation(mode,current.revision,name)))
        self.assertTrue(result.HasField('policy'),result)

    def enroll_bootstrap(self, name='bootstrap-actor', existing_actor=None, profile='starter', revision=1):
        key,public=self.key(name)
        record=self.enroll(self.owner_peer,self.intent(public=public),key,public)
        if record.status==issuer.PENDING:
            kwargs={'revision':self.policy(self.owner_peer).revision}
            if existing_actor is not None: kwargs.update(choice=issuer.APPROVE_EXISTING,target=existing_actor)
            record=self.decide(self.owner_peer,record,**kwargs).enrollment
        claims=self.claims(record,self.info,profile,revision)
        return record,key,public,claims

    def selector(self, revision=1, name='starter', template='entry'):
        return grant.BootstrapSelector(issuer_id=self.info.issuer_id,profile_id=name,
            profile_revision=revision,template_id=template)

    def bind(self, peer, parent, selector=None):
        page=self.own(peer);peer.action_sequence+=1
        return peer.call(bootstrap_bind=session.WorldBootstrapBind(seq=peer.action_sequence,
            selector=selector or self.selector(),parent_id=parent.id,expected_revision=page.revision))

    def binding(self, world, parent=None, **kwargs):
        result=self.bind(world.peers[0],parent or self.root_grant(world.peers[0]),**kwargs)
        self.assertTrue(result.HasField('binding'),result)
        return result.binding

    def apply(self, peer):
        peer.action_sequence+=1
        return peer.call(bootstrap_apply=session.WorldBootstrapApply(seq=peer.action_sequence))

    def ready_bootstrap(self, admission=session.EXPLICIT_ACTORS):
        world=self.scoped_world(admission);self.publish();binding=self.binding(world)
        self.select_profile();enrollment=self.enroll_bootstrap()
        return world,binding,enrollment

    def authenticate_bootstrap(self, peer, enrollment):
        record,key,public,_=enrollment
        return self.authenticate(peer,record,key,public)

    def check_report(self, report, world, profile='starter', revision=1):
        self.assertEqual(report.issuer_id,self.info.issuer_id)
        self.assertEqual(report.profile_id,profile);self.assertEqual(report.profile_revision,revision)
        self.assertEqual(report.domain.kind,grant.WORLD)
        self.assertEqual(report.domain.id,world.management('world.info')['world_id'])
        self.assertFalse(report.HasField('error'),report)
        self.assertEqual([item.template_id for item in report.results],['entry'])
        return report.results[0]


class WorldBootstrapTests(BootstrapWorldFixture):
    def test_bound_profile_admits_private_actor_and_routes_real_navigation(self):
        world,binding,enrollment=self.ready_bootstrap();peer=world.peers[1]
        reply=self.authenticate_bootstrap(peer,enrollment)
        self.assertTrue(reply.HasField('opened'),reply)
        result=self.check_report(reply.bootstrap_attempt,world)
        self.assertTrue(result.HasField('grant'),result)
        self.assertEqual(result.grant.parent_id,binding.parent_id)
        self.assertEqual(result.grant.terms.subject,self.principal(enrollment[3]))
        self.assertTrue(self.navigation(peer).HasField('action'))
        self.assertTrue(peer.act(stop=core.ActorStopRequest()).HasField('action'))
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)

    def test_signed_profile_without_domain_binding_cannot_create_admission(self):
        world=self.scoped_world();self.publish();self.select_profile();actor=self.enroll_bootstrap()
        reply=self.authenticate_bootstrap(world.peers[1],actor)
        self.denied(reply,core.FORBIDDEN)
        result=self.check_report(reply.bootstrap_attempt,world)
        self.assertEqual(result.error.code,core.NOT_FOUND)

    def test_public_entry_reports_unbound_template_without_action_authority(self):
        world=self.scoped_world(session.REGISTERED_ACTORS);self.publish();self.select_profile();actor=self.enroll_bootstrap()
        peer=world.peers[1];reply=self.authenticate_bootstrap(peer,actor)
        self.assertTrue(reply.HasField('opened'),reply)
        self.assertEqual(self.check_report(reply.bootstrap_attempt,world).error.code,core.NOT_FOUND)
        self.assertFalse(self.own(peer).grants)
        self.denied(self.navigation(peer),core.FORBIDDEN)

    def test_binding_requires_authenticated_parent_holder(self):
        world=self.scoped_world(session.REGISTERED_ACTORS);self.publish()
        self.select_profile('empty-v1');actor=self.enroll_bootstrap(profile='empty-v1')
        peer=world.peers[1];self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        self.denied(self.bind(peer,self.root_grant(world.peers[0])),core.FORBIDDEN)

    def test_administration_cannot_bind_without_explicit_delegation(self):
        world=self.scoped_world(roots=lambda:[self.terms(rights=(grant.WORLD_ENTER,grant.POLICY_ADMINISTER),depth=0)])
        self.publish()
        self.denied(self.bind(world.peers[0],self.root_grant(world.peers[0])),core.FORBIDDEN)

    def test_failed_binding_consumes_actor_sequence(self):
        world=self.scoped_world();self.publish();peer=world.peers[0]
        self.denied(self.bind(peer,self.root_grant(peer),self.selector(template='missing')),core.NOT_FOUND)
        self.denied(peer.call(action=core.ActorActionRequest(seq=peer.action_sequence,stop=core.ActorStopRequest())),core.STALE_SEQUENCE)

    def test_anonymous_channels_cannot_administer_or_apply_bootstrap(self):
        world=self.scoped_world();self.publish();peer=world.peers[1]
        self.denied(peer.call(bootstrap_bind=session.WorldBootstrapBind(seq=1,selector=self.selector(),
            parent_id=self.root_grant(world.peers[0]).id,expected_revision=1)),core.UNAUTHORIZED)
        self.denied(peer.call(bootstrap_apply=session.WorldBootstrapApply(seq=2)),core.UNAUTHORIZED)

    def test_challenge_and_failed_proof_do_not_materialize_grants(self):
        world,binding,actor=self.ready_bootstrap();peer=world.peers[1]
        record,_,_,claims=actor
        challenge=peer.call(begin=session.WorldAuthenticationBegin(credential=record.credential)).challenge
        reply=peer.call(complete=issuer.AuthenticationComplete(nonce=challenge.nonce,
            proof=self.proof(challenge,self.owner,self.owner_public)))
        self.denied(reply,core.UNAUTHORIZED)
        self.assertFalse(reply.HasField('bootstrap_attempt'))
        # The next successful authentication must perform the first grant insertion.
        completed=self.authenticate_bootstrap(peer,actor)
        self.assertEqual(self.check_report(completed.bootstrap_attempt,world).grant.created_revision,binding.created_revision+1)

    def test_reconnect_and_explicit_retry_return_same_durable_grant(self):
        world,_,actor=self.ready_bootstrap();peer=world.peers[1]
        opened=self.authenticate_bootstrap(peer,actor)
        original=self.check_report(opened.bootstrap_attempt,world).grant
        first_revision=self.own(peer).revision
        explicit=self.apply(peer)
        self.assertTrue(explicit.HasField('bootstrap'),explicit)
        self.assertEqual(self.check_report(explicit.bootstrap,world).grant,original)
        peer.call(close=session.WorldSessionClose())
        again=self.authenticate_bootstrap(peer,actor)
        self.assertEqual(self.check_report(again.bootstrap_attempt,world).grant,original)
        self.assertEqual(self.own(peer).revision,first_revision)

    def test_server_restart_retains_binding_and_receipt(self):
        world,_,actor=self.ready_bootstrap();reply=self.authenticate_bootstrap(world.peers[1],actor)
        original=self.check_report(reply.bootstrap_attempt,world).grant
        world.close()
        restarted=WorldProcess(self,world.config_path,world.state_path,runtime_config=world.runtime_path)
        self.worlds.append(restarted);restarted.management('system.hello')
        reply=self.authenticate_bootstrap(restarted.peers[0],actor)
        self.assertEqual(self.check_report(reply.bootstrap_attempt,restarted).grant,original)
        self.assertTrue(self.navigation(restarted.peers[0]).HasField('action'))

    def test_parent_revocation_prevents_reconnect_from_restoring_access(self):
        world,_,actor=self.ready_bootstrap();peer=world.peers[1]
        opened=self.authenticate_bootstrap(peer,actor)
        original=self.check_report(opened.bootstrap_attempt,world).grant
        parent=self.root_grant(world.peers[0])
        self.assertTrue(self.revoke_grant(world.peers[0],parent).HasField('grant'))
        self.until(lambda:peer.call(inspect=session.WorldSessionInspect()),lambda r:r.HasField('error'))
        reply=self.authenticate_bootstrap(peer,actor)
        self.denied(reply,core.FORBIDDEN)
        self.assertEqual(self.check_report(reply.bootstrap_attempt,world).grant.id,original.id)

    def test_new_profile_revision_cannot_replace_existing_grant(self):
        world,_,actor=self.ready_bootstrap(session.REGISTERED_ACTORS);peer=world.peers[1]
        original=self.check_report(self.authenticate_bootstrap(peer,actor).bootstrap_attempt,world).grant
        self.publish(rights=(grant.WORLD_ENTER,grant.WORLD_OBSERVE),revision=1)
        self.binding(world,selector=self.selector(revision=2))
        self.select_profile(mode=issuer.MANUAL)
        second=self.enroll_bootstrap(name='second-credential',existing_actor=actor[3].actor_id,revision=2)
        peer.call(close=session.WorldSessionClose());reply=self.authenticate_bootstrap(peer,second)
        self.assertTrue(reply.HasField('opened'),reply)
        self.assertEqual(self.check_report(reply.bootstrap_attempt,world,revision=2).error.code,core.REVISION_CONFLICT)
        self.assertEqual([r.id for r in self.own(peer).grants],[original.id])
        self.denied(peer.act(observe=core.ActorObserveRequest()),core.FORBIDDEN)

    def test_domain_binding_revoke_stops_new_actors_but_preserves_existing_grants(self):
        world,binding,actor=self.ready_bootstrap();peer=world.peers[1]
        self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        owner=world.peers[0];revision=self.own(owner).revision;owner.action_sequence+=1
        result=owner.call(bootstrap_revoke=session.WorldBootstrapRevoke(seq=owner.action_sequence,
            id=binding.id,expected_revision=revision))
        self.assertTrue(result.HasField('binding'),result)
        self.assertGreater(result.binding.revoked_revision,binding.created_revision)
        self.assertTrue(self.navigation(peer).HasField('action'))
        peer.call(close=session.WorldSessionClose())
        newcomer=self.enroll_bootstrap(name='newcomer')
        result=self.authenticate_bootstrap(peer,newcomer)
        self.denied(result,core.FORBIDDEN)
        self.assertEqual(self.check_report(result.bootstrap_attempt,world).error.code,core.FORBIDDEN)

    def test_second_credential_cannot_revive_revoked_bootstrap_grant(self):
        world,_,actor=self.ready_bootstrap(session.REGISTERED_ACTORS);peer=world.peers[1]
        original=self.check_report(self.authenticate_bootstrap(peer,actor).bootstrap_attempt,world).grant
        revoked=self.revoke_grant(world.peers[0],original).grant
        self.assertGreater(revoked.revoked_revision,0)
        self.select_profile(mode=issuer.MANUAL)
        second=self.enroll_bootstrap(name='replacement-key',existing_actor=actor[3].actor_id)
        peer.call(close=session.WorldSessionClose())
        result=self.authenticate_bootstrap(peer,second)
        self.assertTrue(result.HasField('opened'),result)
        self.assertEqual(self.check_report(result.bootstrap_attempt,world).grant,revoked)
        self.denied(self.navigation(peer),core.FORBIDDEN)
        self.assertEqual([r.id for r in self.own(peer).grants],[original.id])

    def test_checked_template_cannot_bind_to_another_world(self):
        world=self.scoped_world();self.publish(domain_id='another-world')
        self.denied(self.bind(world.peers[0],self.root_grant(world.peers[0])),core.FORBIDDEN)


if __name__=='__main__': unittest.main(verbosity=2)
