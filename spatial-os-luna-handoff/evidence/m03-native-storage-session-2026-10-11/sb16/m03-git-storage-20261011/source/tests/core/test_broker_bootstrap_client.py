"""Independent transport peer checks production broker bootstrap reply validation."""
import unittest
import broker_pb2 as broker
import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
import test_broker_control_client as existing


class BootstrapClientFixture(unittest.TestCase):
    setUp=existing.BrokerControlClientTests.setUp
    cleanup=existing.BrokerControlClientTests.cleanup
    send=existing.BrokerControlClientTests.send
    respond=existing.BrokerControlClientTests.respond
    result=existing.BrokerControlClientTests.result
    rejected=existing.BrokerControlClientTests.rejected

    def inputs(self):
        selector=grant.BootstrapSelector(issuer_id=self.principal.issuer_id,profile_id='starter',profile_revision=1,template_id='project')
        terms=grant.GrantTerms(scope=grant.Scope(domain=grant.Domain(kind=grant.WORKSPACE,id='one')),rights=[grant.WORKSPACE_ACCESS])
        binding=grant.BootstrapBinding(id=b'b'*32,selector=selector,parent_id=b'p'*32,authorizer=self.principal,terms=terms,created_revision=3)
        claims=issuer.ActorCredential(protocol_version=1,issuer_id=self.principal.issuer_id,actor_id=self.principal.actor_id,
            credential_id=b'c'*32,public_key=b'k'*32,kind=issuer.HUMAN,bootstrap_profile='starter',bootstrap_profile_revision=1)
        credential=issuer.SignedCredential();credential.content.payload=claims.SerializeToString()
        return binding,credential

    def bind_reply(self, change=None):
        binding,credential=self.inputs()
        request=self.send(bootstrap_bind=broker.BootstrapBindRequest(workspace='one',principal=self.principal,
            selector=binding.selector,parent_id=binding.parent_id,expected_revision=2,credential=credential))
        if change: change(binding)
        self.respond(request,binding=binding)
        if change:self.rejected()
        else:self.assertEqual(self.result('binding'),binding)

    def report_reply(self, change=None):
        binding,credential=self.inputs()
        record=grant.GrantRecord(protocol_version=1,id=b'g'*32,parent_id=binding.parent_id,issuer=binding.authorizer,
            terms=binding.terms,created_revision=4)
        record.terms.subject.CopyFrom(self.principal)
        report=grant.BootstrapReport(issuer_id=self.principal.issuer_id,profile_id='starter',profile_revision=1,
            domain=grant.Domain(kind=grant.WORKSPACE,id='one'),results=[grant.BootstrapGrantResult(template_id='project',grant=record)])
        request=self.send(bootstrap_apply=broker.BootstrapApplyRequest(workspace='one',principal=self.principal,credential=credential))
        if change:change(report)
        self.respond(request,bootstrap=report)
        if change:self.rejected()
        else:self.assertEqual(self.result('bootstrap'),report)


class BrokerBootstrapClientTests(BootstrapClientFixture):
    def test_exact_binding_is_returned(self):self.bind_reply()
    def test_binding_cannot_substitute_parent(self):self.bind_reply(lambda b:setattr(b,'parent_id',b'x'*32))
    def test_binding_cannot_substitute_authorizer(self):self.bind_reply(lambda b:setattr(b.authorizer,'actor_id',b'x'*32))
    def test_binding_cannot_substitute_profile(self):self.bind_reply(lambda b:setattr(b.selector,'profile_id','other'))
    def test_binding_cannot_substitute_workspace(self):self.bind_reply(lambda b:setattr(b.terms.scope.domain,'id','other'))
    def test_binding_revision_must_follow_request(self):self.bind_reply(lambda b:setattr(b,'created_revision',5))
    def test_binding_cannot_assign_recipient(self):self.bind_reply(lambda b:b.terms.subject.CopyFrom(self.principal))
    def test_binding_cannot_return_world_right(self):self.bind_reply(lambda b:b.terms.rights.append(grant.WORLD_ENTER))
    def test_exact_report_is_returned(self):self.report_reply()
    def test_report_cannot_substitute_issuer(self):self.report_reply(lambda r:setattr(r,'issuer_id',b'x'*32))
    def test_report_cannot_substitute_profile(self):self.report_reply(lambda r:setattr(r,'profile_id','other'))
    def test_report_cannot_substitute_revision(self):self.report_reply(lambda r:setattr(r,'profile_revision',2))
    def test_report_cannot_substitute_workspace(self):self.report_reply(lambda r:setattr(r.domain,'id','other'))
    def test_report_cannot_substitute_recipient(self):self.report_reply(lambda r:setattr(r.results[0].grant.terms.subject,'actor_id',b'x'*32))
    def test_report_cannot_supply_root_grant(self):self.report_reply(lambda r:r.results[0].grant.ClearField('parent_id'))
    def test_report_cannot_duplicate_template(self):self.report_reply(lambda r:r.results.add().CopyFrom(r.results[0]))
    def test_report_requires_result_outcome(self):self.report_reply(lambda r:r.results[0].ClearField('grant'))
    def test_report_whole_error_cannot_hide_recorded_grant(self):self.report_reply(lambda r:setattr(r.error,'code',core.NOT_FOUND))
    def test_report_cannot_return_invalid_revocation_revision(self):self.report_reply(lambda r:setattr(r.results[0].grant,'revoked_revision',3))

    def test_recorded_revoked_grant_is_data_not_new_authority(self):
        binding,credential=self.inputs()
        record=grant.GrantRecord(protocol_version=1,id=b'g'*32,parent_id=binding.parent_id,issuer=binding.authorizer,
            terms=binding.terms,created_revision=4,revoked_revision=5)
        record.terms.subject.CopyFrom(self.principal)
        report=grant.BootstrapReport(issuer_id=self.principal.issuer_id,profile_id='starter',profile_revision=1,
            domain=binding.terms.scope.domain,results=[grant.BootstrapGrantResult(template_id='project',grant=record)])
        request=self.send(bootstrap_apply=broker.BootstrapApplyRequest(workspace='one',principal=self.principal,credential=credential))
        self.respond(request,bootstrap=report);self.assertEqual(self.result('bootstrap'),report)

    def test_binding_revoke_matches_identity_and_revision(self):
        binding,credential=self.inputs();binding.revoked_revision=4
        request=self.send(bootstrap_revoke=broker.BootstrapRevokeRequest(workspace='one',principal=self.principal,
            id=binding.id,expected_revision=3,credential=credential))
        self.respond(request,binding=binding);self.assertEqual(self.result('binding'),binding)

    def test_binding_revoke_cannot_substitute_identity(self):
        binding,credential=self.inputs();binding.revoked_revision=4
        request=self.send(bootstrap_revoke=broker.BootstrapRevokeRequest(workspace='one',principal=self.principal,
            id=binding.id,expected_revision=3,credential=credential))
        binding.id=b'x'*32;self.respond(request,binding=binding);self.rejected()


if __name__=='__main__':unittest.main(verbosity=2)
