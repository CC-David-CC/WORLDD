"""Revocation during real broker issuer waits must prevent bootstrap mutations."""
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
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

    def snapshot(self):
        tables=('authority','grant_record','bootstrap_binding','bootstrap_receipt')
        with self.database() as db:
            return {name:tuple(db.execute('SELECT * FROM '+name+' ORDER BY rowid'))
                for name in tables}

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
        before=self.snapshot();counts_before=self.counts()
        relay.armed.set();result=self.apply(peer)
        after=self.snapshot();counts_after=self.counts()
        changed=[name for name in before if before[name]!=after[name]]
        details=(f"reply_text={str(result)!r}; reply_wire_hex={result.SerializeToString().hex()}; "
            f"counts_before={counts_before!r}; counts_after={counts_after!r}; "
            f"changed_tables={changed!r}")
        artifact={
            'case':'workspace-parent-revocation-bootstrap-race' if parent else 'workspace-recipient-revocation-bootstrap-race',
            'reply_text':str(result),
            'reply_wire_hex':result.SerializeToString().hex(),
            'counts_before':counts_before,
            'counts_after':counts_after,
            'changed_tables':changed,
        }
        payload=(json.dumps(artifact,sort_keys=True,separators=(',',':'))+'\n').encode()
        if len(payload)>65536: raise AssertionError('bounded bootstrap race artifact exceeded 64 KiB')
        evidence=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']) if os.environ.get('SPATIAL_SECURITY_EVIDENCE') else self.root
        metadata=evidence.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid!=os.getuid() or
                stat.S_IMODE(metadata.st_mode)!=0o700):
            raise AssertionError('bootstrap race evidence directory must be owned and mode 0700')
        descriptor,artifact_name=tempfile.mkstemp(
            prefix=f'workspace-bootstrap-race-{self._testMethodName}-',suffix='.json',dir=evidence)
        artifact_path=Path(artifact_name)
        try:
            os.fchmod(descriptor,0o600)
            view=memoryview(payload)
            while view:
                written=os.write(descriptor,view)
                if written<=0: raise OSError('incomplete bootstrap race evidence write')
                view=view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        details+=f"; diagnostic_artifact={artifact_path}"
        print("bootstrap race evidence: "+details,flush=True)
        self.assertTrue(relay.delivered.is_set(),details)
        self.assertEqual(relay.remaining,0,details)
        self.assertEqual(counts_before,(1,1,0),details)
        self.assertEqual(counts_after,counts_before,details)
        self.assertEqual(after,before,details)
        if result.HasField('error'):
            accepted=(core.UNAUTHORIZED,core.UNAVAILABLE,core.FORBIDDEN) if parent else (
                core.UNAUTHORIZED,core.UNAVAILABLE)
            self.assertIn(result.error.code,accepted,details)
            if result.error.code==core.FORBIDDEN:
                # Only a revoked parent can invalidate this grant ancestry.
                self.assertTrue(parent,details)
        else:
            self.assertTrue(parent,details)
            report=result.bootstrap
            self.assertEqual(report.issuer_id,self.info.issuer_id,details)
            self.assertEqual(report.profile_id,'starter',details)
            self.assertEqual(report.profile_revision,1,details)
            self.assertEqual(report.domain.kind,grant.WORKSPACE,details)
            self.assertEqual(report.domain.id,'one',details)
            self.assertFalse(report.HasField('error'),details)
            self.assertEqual(len(report.results),1,details)
            denied=report.results[0]
            self.assertEqual(denied.template_id,'project',details)
            self.assertFalse(denied.HasField('grant'),details)
            self.assertTrue(denied.HasField('error'),details)
            self.assertEqual(denied.error.code,core.FORBIDDEN,details)

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
