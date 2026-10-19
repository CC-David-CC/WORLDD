"""Draft: authenticated bootstrap grants enable actual confined workspace actions."""
import unittest
import broker_pb2 as broker
import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
import world_session_pb2 as session
import test_workspace_authority as existing
import test_world_bootstrap as world_bootstrap


class BootstrapWorkspaceFixture(world_bootstrap.BootstrapWorldFixture):
    workspace = existing.WorkspaceAuthorityTests.workspace
    workspace_terms = existing.WorkspaceAuthorityTests.workspace_terms
    own = existing.WorkspaceAuthorityTests.own
    select = existing.WorkspaceAuthorityTests.select
    launch_process = existing.WorkspaceAuthorityTests.launch_process
    wait_job = existing.WorkspaceAuthorityTests.wait_job
    revoke_grant = existing.WorkspaceAuthorityTests.revoke_grant
    make_broker = existing.WorkspaceAuthorityTests.make_broker

    def setUp(self):
        super().setUp();self.brokers=[]
        self.addCleanup(lambda:([w.close() for w in reversed(self.worlds)],
            [b.close() for b in reversed(self.brokers)]))

    def publish(self, rights=None, revision=0, name='starter', domain_id=None):
        terms=self.workspace_terms(rights=rights or (grant.WORKSPACE_ACCESS,*existing.PROJECT),depth=0)
        terms.ClearField('subject')
        if domain_id is not None: terms.scope.domain.id=domain_id
        profile=issuer.BootstrapProfile(id=name,grants=[issuer.BootstrapGrantTemplate(id='project',terms=terms)])
        operation=issuer.IssuerOperation(profile_put=issuer.BootstrapProfilePut(profile=profile,expected_revision=revision))
        # Setup may outlast the issuer's idle limit. Start a new proof, without replaying a mutation.
        self.owner_peer.close();self.owner_peer=self.connect()
        reply=self.execute(self.owner_peer,self.challenge(self.owner_peer,operation))
        self.assertTrue(reply.HasField('profile'),reply);return reply.profile

    def selector(self, revision=1, name='starter', template='project'):
        return super().selector(revision,name,template)

    def bind(self, peer, parent, selector=None):
        page=self.own(peer);peer.action_sequence+=1
        return peer.call(bootstrap_bind=session.WorldBootstrapBind(workspace='one',seq=peer.action_sequence,
            selector=selector or self.selector(),parent_id=parent.id,expected_revision=page.revision))

    def apply(self, peer, workspace='one'):
        peer.action_sequence+=1
        return peer.call(bootstrap_apply=session.WorldBootstrapApply(seq=peer.action_sequence,workspace=workspace))

    def check_report(self, report):
        self.assertEqual(report.issuer_id,self.info.issuer_id)
        self.assertEqual(report.profile_id,'starter');self.assertEqual(report.profile_revision,1)
        self.assertEqual(report.domain.kind,grant.WORKSPACE);self.assertEqual(report.domain.id,'one')
        self.assertFalse(report.HasField('error'),report)
        self.assertEqual([item.template_id for item in report.results],['project'])
        return report.results[0]

    def ready_workspace(self, rights=None):
        world=self.workspace();self.publish(rights=rights);binding=self.binding(world)
        self.select_profile();actor=self.enroll_bootstrap();peer=world.peers[1]
        self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        return world,binding,actor


class WorkspaceBootstrapTests(BootstrapWorkspaceFixture):
    def test_authorization_fence_tracks_bootstrap_binding_revision(self):
        world = self.workspace()
        self.publish()
        principal = self.principal()
        request = broker.AuthorizationFenceRequest(principal=principal)
        before = self.broker.request(authorization_fence=request).authorization_fence
        self.binding(world)
        after = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual(after.incarnation, before.incarnation)
        self.assertGreater(after.epoch, before.epoch)
        repeated = self.broker.request(authorization_fence=request).authorization_fence
        self.assertEqual((repeated.incarnation, repeated.epoch), (after.incarnation, after.epoch))

    def test_explicit_application_enables_confined_real_file_write(self):
        world,binding,actor=self.ready_workspace();peer=world.peers[1]
        self.assertFalse(self.own(peer).grants)
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)
        self.denied(peer.act(workspace_select=core.WorkspaceIdRequest(id='one')),core.FORBIDDEN)
        result=self.apply(peer);self.assertTrue(result.HasField('bootstrap'),result)
        recorded=self.check_report(result.bootstrap).grant
        self.assertEqual(recorded.parent_id,binding.parent_id)
        self.assertEqual(recorded.terms.subject,self.principal(actor[3]))
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)
        self.select(peer)
        process=self.launch_process(peer,"from pathlib import Path\nassert Path.cwd()==Path('/project')\nPath('bootstrap-file').write_text('confined actor')")
        self.wait_job(world,process)
        self.assertEqual((self.project/'bootstrap-file').read_text(),'confined actor')

    def test_application_does_not_make_workspace_available(self):
        world,_,_=self.ready_workspace();peer=world.peers[1]
        world.management('workspace.available',id='one',available=False)
        self.assertTrue(self.apply(peer).HasField('bootstrap'))
        self.denied(peer.act(workspace_select=core.WorkspaceIdRequest(id='one')),core.UNAVAILABLE)
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)
        world.management('workspace.available',id='one',available=True)
        self.select(peer)

    def test_retry_preserves_receipt_and_parent_revocation_denies_execution(self):
        world,binding,_=self.ready_workspace();peer=world.peers[1]
        first=self.check_report(self.apply(peer).bootstrap).grant
        revision=self.own(peer).revision
        self.assertEqual(self.check_report(self.apply(peer).bootstrap).grant,first)
        self.assertEqual(self.own(peer).revision,revision)
        self.select(peer)
        parent=next(r for r in self.own(world.peers[0]).grants if r.id==binding.parent_id)
        self.revoke_grant(world.peers[0],parent)
        self.assertEqual(self.check_report(self.apply(peer).bootstrap).grant,first)
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)
        self.denied(peer.act(workspace_select=core.WorkspaceIdRequest(id='one')),core.FORBIDDEN)
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/bin/true'],operation_id='denied',profile='development')),core.NO_WORKSPACE)

    def test_read_only_bootstrap_does_not_authorize_writable_application(self):
        world,_,_=self.ready_workspace(rights=[grant.WORKSPACE_ACCESS,grant.FILE_BROWSE,grant.FILE_READ,grant.PROCESS_EXECUTE])
        peer=world.peers[1];self.assertTrue(self.apply(peer).HasField('bootstrap'));self.select(peer)
        code="from pathlib import Path\nassert Path('README.txt').read_text()=='base\\n'\ntry:Path('README.txt').write_text('escape')\nexcept OSError:pass\nelse:raise SystemExit(20)"
        for profile in ('development','read-only'):
            self.wait_job(world,self.launch_process(peer,code,operation='restricted-'+profile,profile=profile))
        self.assertEqual((self.project/'README.txt').read_text(),'base\n')

    def test_unknown_workspace_never_redirects_application_to_current_world(self):
        world,_,_=self.ready_workspace();peer=world.peers[1]
        self.denied(self.apply(peer,workspace='unknown-workspace'),core.FORBIDDEN)
        self.assertFalse(self.own(peer).grants)
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)

    def test_broker_and_world_restart_preserve_binding_receipt_and_real_file(self):
        world,_,actor=self.ready_workspace();peer=world.peers[1]
        original=self.check_report(self.apply(peer).bootstrap).grant;self.select(peer)
        self.wait_job(world,self.launch_process(peer,"from pathlib import Path\nPath('durable-bootstrap').write_text('saved')"))
        revision=self.own(peer).revision;world.close();self.broker.close()
        self.broker=existing.BrokerProcess(self,self.execution,self.root/'workspace-policy.textproto')
        restarted=existing.WorldProcess(self,world.config_path,world.state_path,runtime_config=world.runtime_path,broker=self.broker)
        self.worlds.append(restarted);restarted.management('system.hello');peer=restarted.peers[0]
        self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        self.assertEqual(self.check_report(self.apply(peer).bootstrap).grant,original)
        self.assertEqual(self.own(peer).revision,revision)
        self.assertEqual(peer.call(inspect=session.WorldSessionInspect()).state.workspace,'one')
        self.assertEqual((self.project/'durable-bootstrap').read_text(),'saved')

    def test_second_credential_does_not_revive_revoked_receipt(self):
        world,_,actor=self.ready_workspace();peer=world.peers[1]
        original=self.check_report(self.apply(peer).bootstrap).grant
        revoked=self.revoke_grant(world.peers[0],original)
        self.select_profile(mode=issuer.MANUAL)
        second=self.enroll_bootstrap(name='replacement-credential',existing_actor=actor[3].actor_id)
        peer.call(close=session.WorldSessionClose());self.assertTrue(self.authenticate_bootstrap(peer,second).HasField('opened'))
        self.assertEqual(self.check_report(self.apply(peer).bootstrap).grant,revoked)
        self.denied(peer.act(workspace_select=core.WorkspaceIdRequest(id='one')),core.FORBIDDEN)
        self.assertEqual([item.id for item in self.own(peer).grants],[original.id])

    def test_updated_profile_cannot_replace_prior_actor_receipt(self):
        world,_,actor=self.ready_workspace();peer=world.peers[1]
        original=self.check_report(self.apply(peer).bootstrap).grant
        self.publish(rights=[grant.WORKSPACE_ACCESS,grant.FILE_READ],revision=1)
        self.binding(world,selector=self.selector(revision=2));self.select_profile(mode=issuer.MANUAL)
        second=self.enroll_bootstrap(name='updated-profile-credential',existing_actor=actor[3].actor_id,revision=2)
        peer.call(close=session.WorldSessionClose());self.assertTrue(self.authenticate_bootstrap(peer,second).HasField('opened'))
        result=self.apply(peer);self.assertTrue(result.HasField('bootstrap'),result)
        self.assertEqual(result.bootstrap.profile_revision,2)
        self.assertEqual(result.bootstrap.results[0].error.code,core.REVISION_CONFLICT)
        self.assertEqual([item.id for item in self.own(peer).grants],[original.id])

    def test_broker_rejects_mismatched_missing_and_forged_credentials(self):
        world,_,actor=self.ready_workspace();world.close()
        principal=self.principal(actor[3])
        request=broker.BootstrapApplyRequest(workspace='one',principal=principal,credential=self.record.credential)
        self.denied(self.broker.request(bootstrap_apply=request),core.INVALID_ARGUMENT)
        request.ClearField('credential');self.denied(self.broker.request(bootstrap_apply=request),core.INVALID_ARGUMENT)
        request.credential.CopyFrom(actor[0].credential)
        claims=issuer.ActorCredential.FromString(request.credential.content.payload);claims.bootstrap_profile='forged'
        request.credential.content.payload=claims.SerializeToString()
        self.denied(self.broker.request(bootstrap_apply=request),core.INVALID_ARGUMENT)
        self.assertFalse(self.broker.request(grants=broker.GrantListRequest(workspace='one',principal=principal)).grants.grants)
        request.credential.CopyFrom(actor[0].credential)
        self.assertTrue(self.check_report(self.broker.request(bootstrap_apply=request).bootstrap).HasField('grant'))

    def test_actor_cannot_bind_another_holders_parent(self):
        world=self.workspace();self.publish();parent=self.root_grant(world.peers[0]);self.select_profile()
        actor=self.enroll_bootstrap();peer=world.peers[1];self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        self.denied(self.bind(peer,parent),core.FORBIDDEN)
        self.assertEqual(self.check_report(self.apply(peer).bootstrap).error.code,core.NOT_FOUND)
        self.assertFalse(self.own(peer).grants)

    def test_binding_revocation_blocks_new_recipients_but_preserves_existing_access(self):
        world,binding,actor=self.ready_workspace();peer=world.peers[1]
        original=self.check_report(self.apply(peer).bootstrap).grant
        owner=world.peers[0];revision=self.own(owner).revision;owner.action_sequence+=1
        result=owner.call(bootstrap_revoke=session.WorldBootstrapRevoke(workspace='one',seq=owner.action_sequence,
            id=binding.id,expected_revision=revision))
        self.assertTrue(result.HasField('binding'),result)
        self.assertEqual(self.check_report(self.apply(peer).bootstrap).grant,original);self.select(peer)
        peer.call(close=session.WorldSessionClose());newcomer=self.enroll_bootstrap(name='newcomer')
        self.assertTrue(self.authenticate_bootstrap(peer,newcomer).HasField('opened'))
        self.assertEqual(self.check_report(self.apply(peer).bootstrap).error.code,core.FORBIDDEN)
        self.denied(peer.act(workspace_select=core.WorkspaceIdRequest(id='one')),core.FORBIDDEN)


if __name__=='__main__': unittest.main(verbosity=2)
