"""Exact broker authority domains and genuine revocation during issuer replies."""
from pathlib import Path
import subprocess
import time
import unittest
import broker_pb2 as broker
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as issuer
from harness.git_fixture import GitRepo
from test_workspace_participant import WorkspaceParticipantFixture,BrokerProcess
from test_world_bootstrap_races import BootstrapRelay
import test_workspace_authority as workspace

class WorkspaceParticipantRaceTests(WorkspaceParticipantFixture):
    def second_binding(self):
        first=BrokerProcess(self,self.execution)
        repository=GitRepo(self.root/'second-project')
        first.call('git.repo.import',id='second-repo',path=str(repository.path))
        first.call('git.worktree.create',repo='second-repo',id='second-tree',branch='second-app',start='main',operation_id='second-tree')
        first.call('workspace.create',id='two',worktree='second-tree',policy=workspace.POLICY)
        first.close();self.assertEqual(first.proc.returncode,0)
        directory=self.root/'second-authority';directory.mkdir(mode=0o700)
        terms=self.workspace_terms();terms.scope.domain.id='two'
        seed=broker.WorkspaceAuthoritySeed(protocol_version=1,workspace='two',authority_directory=str(directory),roots=[terms])
        path=self.write('second-seed',seed)
        result=subprocess.run([str(workspace.BROKER),'--state-dir',str(self.execution),'--init-authority',str(path)],capture_output=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)
        return broker.WorkspaceAuthorityBinding(workspace='two',authority_directory=str(directory))
    def access(self,workspace_id='one'):
        principal=self.principal();actor='identity:'+principal.issuer_id.hex()+':'+principal.actor_id.hex()
        return self.broker.request(access=broker.AccessRequest(workspace=workspace_id,actor=actor,principal=principal))
    def test_registered_service_rejects_a_different_workspace(self):
        other=self.second_binding();item=self.start_broker(bindings=[other])
        self.assertEqual(item.proc.wait(timeout=4),2);self.assertEqual(self.activations(),[])
        self.assertIn('Workspace participation is unavailable',Path(item.log.name).read_text())
    def test_registration_must_cover_every_configured_workspace(self):
        other=self.second_binding();one=broker.WorkspaceAuthorityBinding(workspace='one',authority_directory=str(self.directory))
        item=self.start_broker(bindings=[one,other]);self.assertEqual(item.proc.wait(timeout=4),2)
        self.assertEqual(self.activations(),[])
    def delayed_status(self,kind):
        pending=[]
        def revoke():
            self.revoke_now(kind)
            self.assertTrue(relay.delivered.wait(.3))
            time.sleep(.08)
            pending.append(self.barrier())
            self.assertEqual((pending[-1].participant_count,pending[-1].pending_count,pending[-1].complete),(1,1,False))
        relay=BootstrapRelay(self,revoke,'actor_status')
        item=self.start_broker(relay=relay,query_relay=relay);item.call('system.hello')
        poll,count=self.launch_application();relay.armed.set()
        result=self.access();self.assertTrue(result.HasField('error'),result)
        self.assertEqual(result.error.code,core.UNAVAILABLE)
        self.assertEqual(len(pending),1);self.assertTrue(relay.delivered.is_set())
        completed=self.until(self.barrier,lambda value:value.complete);self.assertEqual(completed.participant_count,1)
        if kind==issuer.ACTOR_TARGET:self.assertEqual(len(poll.poll(0)),count)
        else:
            self.assertEqual(poll.poll(0),[])
            self.assertTrue(self.access().HasField('access'))
        item.close();self.assertEqual(item.proc.returncode,0);self.assertEqual(len(poll.poll(0)),count)
    def test_actor_revocation_during_delayed_reply_waits_for_handler_and_process_exit(self):
        self.delayed_status(issuer.ACTOR_TARGET)
    def test_credential_revocation_rejects_late_reply_without_killing_actor_execution(self):
        self.delayed_status(issuer.CREDENTIAL_TARGET)

class WorkspaceParticipantMultiTests(WorkspaceParticipantFixture):
    second_binding=WorkspaceParticipantRaceTests.second_binding
    access=WorkspaceParticipantRaceTests.access
    def service_domains(self):
        return [grants.Domain(kind=grants.WORKSPACE,id='one'),grants.Domain(kind=grants.WORKSPACE,id='two')]
    def test_exact_multi_workspace_registration_allows_both_bindings(self):
        other=self.second_binding();one=broker.WorkspaceAuthorityBinding(workspace='one',authority_directory=str(self.directory))
        item=self.start_broker(bindings=[other,one]);item.call('system.hello')
        for name in ['one','two']:
            result=self.access(name);self.assertTrue(result.HasField('access'),result)
            self.assertIn(core.PROCESS_LAUNCH,result.access.rights)
        self.assertEqual(self.activations(),[(1,0,1)])
        item.close();self.assertEqual(item.proc.returncode,0);self.assertEqual(self.activations(),[(1,0,0)])

if __name__=='__main__':unittest.main(verbosity=2)
