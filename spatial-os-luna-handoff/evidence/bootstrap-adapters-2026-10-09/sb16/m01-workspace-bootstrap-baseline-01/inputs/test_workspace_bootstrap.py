"""Draft: authenticated bootstrap grants enable actual confined workspace actions."""
import unittest
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
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/bin/true'],operation_id='denied',profile='development')),core.FORBIDDEN)

    def test_read_only_bootstrap_does_not_authorize_writable_application(self):
        world,_,_=self.ready_workspace(rights=[grant.WORKSPACE_ACCESS,grant.FILE_BROWSE,grant.FILE_READ,grant.PROCESS_EXECUTE])
        peer=world.peers[1];self.assertTrue(self.apply(peer).HasField('bootstrap'));self.select(peer)
        self.denied(peer.act(process_launch=core.ProcessLaunchRequest(argv=['/bin/true'],operation_id='writable-denied',profile='development')),core.FORBIDDEN)
        code="from pathlib import Path\nassert Path('README.txt').read_text()=='base\\n'\ntry:Path('README.txt').write_text('escape')\nexcept OSError:pass\nelse:raise SystemExit(20)"
        self.wait_job(world,self.launch_process(peer,code,profile='read-only'))
        self.assertEqual((self.project/'README.txt').read_text(),'base\n')

    def test_unknown_workspace_never_redirects_application_to_current_world(self):
        world,_,_=self.ready_workspace();peer=world.peers[1]
        self.denied(self.apply(peer,workspace='unknown-workspace'),core.FORBIDDEN)
        self.assertFalse(self.own(peer).grants)
        self.assertFalse(peer.call(inspect=session.WorldSessionInspect()).state.workspace)


if __name__=='__main__': unittest.main(verbosity=2)
