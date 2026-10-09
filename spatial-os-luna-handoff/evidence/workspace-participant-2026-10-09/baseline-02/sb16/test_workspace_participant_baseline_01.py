"""Broker participation uses real issuer obligations and confined process witnesses."""
import os
from pathlib import Path
import select
import socket
import sqlite3
import subprocess
import time
import unittest
from google.protobuf import text_format
import broker_pb2 as broker
import core_pb2 as core
import grants_pb2 as grants
import issuer_pb2 as issuer
from harness.git_fixture import GitRepo
from test_world_identity import WorldIdentityFixture
from test_world_participant import HeldRevocationRelay
import test_workspace_authority as workspace

RUN = """from pathlib import Path
import subprocess,time
subprocess.Popen(['/usr/bin/python3','-c','import time; time.sleep(60)'],start_new_session=True)
while True:
    Path('/project/heartbeat').write_text(str(time.monotonic_ns()))
    time.sleep(.01)
"""

class BrokerProcess(workspace.BrokerProcess):
    def close(self):
        if self.stream.fileno()<0:return
        if self.proc.poll() is None:
            try:self.stream.shutdown(socket.SHUT_WR)
            except OSError:pass
            try:self.proc.wait(timeout=6)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3);raise
        self.stream.close();self.log.close()

class WorkspaceParticipantFixture(WorldIdentityFixture):
    principal=workspace.WorkspaceAuthorityTests.principal
    workspace_terms=workspace.WorkspaceAuthorityTests.workspace_terms
    descendants=workspace.WorkspaceAuthorityTests.descendants
    def setUp(self):
        super().setUp();self.brokers=[]
        self.addCleanup(lambda:[item.close() for item in reversed(self.brokers)])
        self.execution=self.root/'execution';first=BrokerProcess(self,self.execution)
        repo=GitRepo(self.root)
        first.call('git.repo.import',id='repo',path=str(repo.path))
        tree=first.call('git.worktree.create',repo='repo',id='tree',branch='app',start='main',operation_id='tree')
        self.project=Path(tree['path']);first.call('workspace.create',id='one',worktree='tree',policy=workspace.POLICY)
        first.close();self.assertEqual(first.proc.returncode,0)
        self.directory=self.root/'authority';self.directory.mkdir(mode=0o700)
        seed=broker.WorkspaceAuthoritySeed(protocol_version=1,workspace='one',authority_directory=str(self.directory),roots=[self.workspace_terms()])
        path=self.write('seed',seed)
        result=subprocess.run([str(workspace.BROKER),'--state-dir',str(self.execution),'--init-authority',str(path)],capture_output=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)
        self.signer,public=self.key('broker-service')
        self.definition=issuer.AuthorityParticipant(id=b'b'*32,public_key=public,domains=[grants.Domain(kind=grants.WORKSPACE,id='one')])
        peer=self.connect()
        try:
            op=issuer.IssuerOperation(participant_register=issuer.AuthorityParticipantRegister(participant=self.definition,expected_registry_revision=0))
            result=self.execute(peer,self.challenge(peer,op));self.assertTrue(result.HasField('participant'),result)
        finally:peer.close()
    def write(self,name,message):
        path=self.root/(name+'.textproto');path.write_text(text_format.MessageToString(message),encoding='utf-8');path.chmod(0o600);return path
    def start_broker(self,relay=None,participate=True):
        query=self.root/'query.textproto';query.write_bytes(self.config_file().read_bytes());query.chmod(0o600)
        service=self.root/'service.textproto';service.write_bytes(self.config_file(endpoint_port=relay.port if relay else None,io_timeout_ms=3000).read_bytes());service.chmod(0o600)
        participant=self.write('participant',issuer.AuthorityParticipantRuntimeConfig(protocol_version=1,
            issuer_client_config=str(service),private_key_path=str(self.signer),participant_id=self.definition.id,registered_revision=1))
        config=broker.WorkspaceAuthorityConfig(protocol_version=1,issuer_client_config=str(query),
            bindings=[broker.WorkspaceAuthorityBinding(workspace='one',authority_directory=str(self.directory))])
        # Old binaries have no participant field. This permits an unchanged-source counterexample.
        if participate and 'participant_runtime_config' in config.DESCRIPTOR.fields_by_name:config.participant_runtime_config=str(participant)
        elif relay:config.issuer_client_config=str(service)
        self.broker=BrokerProcess(self,self.execution,self.write('broker',config));return self.broker
    def activations(self):
        with sqlite3.connect(self.state/'identity.db') as db:return db.execute('SELECT generation,applied_revision,active FROM authority_activation ORDER BY generation').fetchall()
    def barrier(self):
        peer=self.connect()
        try:
            op=issuer.IssuerOperation(barrier_read=issuer.AuthorityBarrierRead(revision=1))
            result=self.execute(peer,self.challenge(peer,op));self.assertTrue(result.HasField('authority_barrier'),result);return result.authority_barrier
        finally:peer.close()
    def revoke_now(self,kind=issuer.ACTOR_TARGET):
        self.owner_peer=self.connect()
        try:self.revoke(kind=kind)
        finally:self.owner_peer.close()
    def launch(self):
        principal=self.principal();actor='identity:'+principal.issuer_id.hex()+':'+principal.actor_id.hex()
        result=self.broker.request(launch=broker.LaunchRequest(workspace='one',actor=actor,principal=principal,
            command=core.ProcessLaunchRequest(operation_id='run',profile='development',argv=['/usr/bin/python3','-c',RUN])))
        self.assertTrue(result.HasField('launched'),result)
        self.until(lambda:(self.project/'heartbeat').exists(),bool)
        pids=self.until(self.descendants,lambda value:len(value)>=5)
        poll=select.poll()
        for pid in pids:
            fd=os.pidfd_open(pid);self.addCleanup(os.close,fd);poll.register(fd,select.POLLIN)
        self.assertEqual(poll.poll(0),[])
        return poll,len(pids)

class WorkspaceParticipantTests(WorkspaceParticipantFixture):
    def test_authority_broker_requires_participant_configuration(self):
        item=self.start_broker(participate=False)
        try:code=item.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:self.fail('Authority broker remained available without participation')
        self.assertEqual(code,2);self.assertIn('participant configuration is required',Path(item.log.name).read_text())
    def test_broker_activates_before_protected_work_and_cleanly_releases(self):
        item=self.start_broker();item.call('system.hello');self.assertEqual(self.activations(),[(1,0,1)])
        item.close();self.assertEqual(item.proc.returncode,0);self.assertEqual(self.activations(),[(1,0,0)])
    def test_completion_waits_for_real_detached_process_exit(self):
        relay=HeldRevocationRelay(self);item=self.start_broker(relay);item.call('system.hello');poll,count=self.launch()
        self.revoke_now();self.assertTrue(relay.held.wait(.3));self.assertFalse(relay.delivered.is_set())
        pending=self.barrier();before=(self.project/'heartbeat').read_text();time.sleep(.08)
        after=(self.project/'heartbeat').read_text();self.assertNotEqual(before,after);self.assertEqual(poll.poll(0),[])
        self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False))
        relay.release.set();self.assertTrue(relay.delivered.wait(.3))
        completed=self.until(self.barrier,lambda value:value.complete);self.assertEqual(completed.participant_count,1)
        self.assertEqual(len(poll.poll(0)),count,'Issuer completion preceded independently witnessed process exit')
        item.close();self.assertEqual(item.proc.returncode,0);self.assertEqual(self.activations(),[(1,1,0)])
    def test_credential_revocation_preserves_actor_owned_execution(self):
        item=self.start_broker();item.call('system.hello');poll,count=self.launch();self.revoke_now(issuer.CREDENTIAL_TARGET)
        completed=self.until(self.barrier,lambda value:value.complete);self.assertEqual(completed.participant_count,1)
        self.assertEqual(poll.poll(0),[]);before=(self.project/'heartbeat').read_text();time.sleep(.08)
        self.assertNotEqual(before,(self.project/'heartbeat').read_text())
        item.close();self.assertEqual(item.proc.returncode,0);self.assertEqual(len(poll.poll(0)),count)
        self.assertEqual(self.activations(),[(1,1,0)])
    def test_clean_restart_uses_new_generation(self):
        item=self.start_broker();item.call('system.hello');item.close();self.assertEqual(item.proc.returncode,0)
        item=self.start_broker();item.call('system.hello');self.assertEqual(self.activations(),[(1,0,0),(2,0,1)])
    def test_crash_retains_obligation_and_blocks_replacement(self):
        item=self.start_broker();item.call('system.hello');item.proc.kill();item.proc.wait(timeout=2);item.close()
        self.revoke_now();pending=self.barrier();self.assertEqual((pending.participant_count,pending.pending_count),(1,1))
        replacement=self.start_broker();self.assertEqual(replacement.proc.wait(timeout=4),2)
        self.assertFalse(self.barrier().complete);self.assertEqual(self.activations(),[(1,0,1)])

if __name__=='__main__':unittest.main(verbosity=2)
