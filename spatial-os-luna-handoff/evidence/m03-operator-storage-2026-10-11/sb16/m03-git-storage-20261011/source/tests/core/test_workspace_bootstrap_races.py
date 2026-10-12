"""Revocation during real broker issuer waits must prevent bootstrap mutations."""
import sqlite3
import unittest
import core_pb2 as core
import grants_pb2 as grant
import issuer_pb2 as issuer
import world_session_pb2 as session
from test_workspace_bootstrap import BootstrapWorkspaceFixture
from test_world_bootstrap_races import BootstrapRelay
import test_workspace_authority as existing


class WorkspaceBootstrapRaceTests(BootstrapWorkspaceFixture):
    def database(self):
        path=self.root/'workspace-authority/authority.db'
        self.assertTrue(path.is_file(),path)
        return sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)

    def counts(self):
        with self.database() as db:
            return tuple(db.execute('SELECT count(*) FROM '+name).fetchone()[0]
                for name in ('grant_record','bootstrap_binding','bootstrap_receipt'))

    def revoke_identity(self, claims, parent=False):
        target=issuer.IdentityReference(kind=issuer.ACTOR_TARGET if parent else issuer.CREDENTIAL_TARGET,
            id=claims.actor_id if parent else claims.credential_id)
        operation=issuer.IssuerOperation(identity_revoke=issuer.IdentityRevoke(target=target,
            expected_security_revision=0,reason='workspace bootstrap delayed reply regression'))
        peer=self.connect()
        self.assertTrue(self.execute(peer,self.challenge(peer,operation)).HasField('identity_status'))

    def apply_race(self, operation, parent=False, occurrence=1):
        holder={}
        relay=BootstrapRelay(self,lambda:self.revoke_identity(self.identity if parent else holder['actor'][3],parent),
            operation,occurrence)
        world=self.workspace(issuer_port=relay.port);self.publish();self.binding(world)
        self.select_profile();actor=self.enroll_bootstrap();holder['actor']=actor;peer=world.peers[1]
        self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        relay.armed.set();result=self.apply(peer)
        if result.HasField('error'):
            self.assertIn(result.error.code,(core.UNAUTHORIZED,core.UNAVAILABLE))
        else:
            self.assertTrue(parent,result)
            self.assertEqual(self.check_report(result.bootstrap).error.code,core.FORBIDDEN)
        self.assertTrue(relay.delivered.is_set());self.assertEqual(relay.remaining,0)
        self.assertEqual(self.counts(),(1,1,0))

    def test_credential_revoked_during_broker_profile_read_creates_no_grant(self):self.apply_race('profile_read')
    def test_parent_actor_revoked_during_broker_profile_read_creates_no_grant(self):self.apply_race('profile_read',parent=True)
    def test_credential_revoked_during_broker_parent_read_creates_no_grant(self):self.apply_race('actor_status',occurrence=2)
    def test_parent_actor_revoked_during_broker_parent_read_creates_no_grant(self):self.apply_race('actor_status',parent=True,occurrence=2)

    def test_binding_credential_revoked_during_profile_read_creates_no_binding(self):
        relay=BootstrapRelay(self,lambda:self.revoke_identity(self.identity),'profile_read')
        world=self.workspace(issuer_port=relay.port);self.publish();parent=self.root_grant(world.peers[0])
        relay.armed.set();result=self.bind(world.peers[0],parent)
        self.assertTrue(result.HasField('error'),result);self.assertIn(result.error.code,(core.UNAUTHORIZED,core.UNAVAILABLE))
        self.assertTrue(relay.delivered.is_set());self.assertEqual(self.counts(),(1,0,0))

    def test_binding_revoke_credential_race_keeps_binding_active(self):
        relay=BootstrapRelay(self,lambda:self.revoke_identity(self.identity),'actor_status')
        world=self.workspace(issuer_port=relay.port);self.publish();binding=self.binding(world);peer=world.peers[0]
        revision=self.own(peer).revision;peer.action_sequence+=1;relay.armed.set()
        result=peer.call(bootstrap_revoke=session.WorldBootstrapRevoke(seq=peer.action_sequence,workspace='one',
            id=binding.id,expected_revision=revision))
        self.assertTrue(result.HasField('error'),result);self.assertIn(result.error.code,(core.UNAUTHORIZED,core.UNAVAILABLE))
        self.assertTrue(relay.delivered.is_set());self.assertEqual(self.counts(),(1,1,0))
        with self.database() as db:
            value=grant.BootstrapBinding.FromString(db.execute('SELECT record FROM bootstrap_binding WHERE id=?',(binding.id,)).fetchone()[0])
        self.assertEqual(value,binding)

    def running_process_survives_credential_race(self, operation, ordinary_access=False):
        holder={}
        relay=BootstrapRelay(self,lambda:self.revoke_identity(holder['actor'][3]),operation)
        world=self.workspace(issuer_port=relay.port);self.publish();self.binding(world);self.select_profile()
        actor=self.enroll_bootstrap();holder['actor']=actor;peer=world.peers[1]
        self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        self.assertTrue(self.apply(peer).HasField('bootstrap'));self.select(peer)
        code="from pathlib import Path\nimport time\nPath('ready').touch()\nwhile not Path('release').exists():time.sleep(.01)\nPath('done').write_text('survived')"
        process=self.launch_process(peer,code)
        self.until(lambda:(self.project/'ready').exists(),bool)
        relay.armed.set()
        result=peer.act(workspace_select=core.WorkspaceIdRequest(id='one')) if ordinary_access else self.apply(peer)
        self.assertTrue(result.HasField('error'),result);self.assertIn(result.error.code,(core.UNAUTHORIZED,core.UNAVAILABLE))
        self.assertTrue(relay.delivered.is_set());self.assertEqual(self.counts(),(2,1,1))
        (self.project/'release').touch();self.wait_job(world,process)
        self.assertEqual((self.project/'done').read_text(),'survived')

    def test_credential_revoked_during_credential_reply_preserves_authorized_process(self):
        self.running_process_survives_credential_race('credential_check')

    def test_credential_revoked_during_actor_reply_preserves_authorized_process(self):
        self.running_process_survives_credential_race('actor_status')

    def test_ordinary_workspace_access_race_preserves_independent_process_authority(self):
        self.running_process_survives_credential_race('actor_status',ordinary_access=True)

    def test_parent_actor_revocation_during_reply_preserves_independent_process_grant(self):
        initial=self.enroll_bootstrap(name='independent-actor',profile='empty-v1')
        relay=BootstrapRelay(self,lambda:self.revoke_identity(self.identity,parent=True),'actor_status')
        world=self.workspace(issuer_port=relay.port,roots=lambda:[self.workspace_terms(),
            self.workspace_terms(initial[3],rights=[grant.WORKSPACE_ACCESS,*existing.PROJECT],depth=0)])
        self.publish();self.binding(world);self.select_profile(mode=issuer.MANUAL)
        actor=self.enroll_bootstrap(name='independent-profile',existing_actor=initial[3].actor_id);peer=world.peers[1]
        self.assertTrue(self.authenticate_bootstrap(peer,actor).HasField('opened'))
        self.assertTrue(self.apply(peer).HasField('bootstrap'));self.select(peer)
        process=self.launch_process(peer,"from pathlib import Path\nimport time\nPath('ready').touch()\nwhile not Path('release').exists():time.sleep(.01)\nPath('done').write_text('independent')")
        self.until(lambda:(self.project/'ready').exists(),bool);relay.armed.set();result=self.apply(peer)
        self.assertTrue(result.HasField('error'),result);self.assertEqual(result.error.code,core.UNAVAILABLE)
        self.assertTrue(relay.delivered.is_set());self.assertEqual(self.counts(),(3,1,1))
        (self.project/'release').touch();self.wait_job(world,process)
        self.assertEqual((self.project/'done').read_text(),'independent')


if __name__=='__main__':unittest.main(verbosity=2)
