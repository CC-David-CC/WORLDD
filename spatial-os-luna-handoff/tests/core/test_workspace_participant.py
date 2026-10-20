"""Broker participation uses real issuer obligations and confined process witnesses."""
import errno
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
from harness.wire import frame, make_request
from test_world_identity import receive
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
        if self.resources:return workspace.BrokerProcess.close(self)
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
        self.definition=issuer.AuthorityParticipant(id=b'b'*32,public_key=public,domains=self.service_domains())
        peer=self.connect()
        try:
            op=issuer.IssuerOperation(participant_register=issuer.AuthorityParticipantRegister(participant=self.definition,expected_registry_revision=0))
            result=self.execute(peer,self.challenge(peer,op));self.assertTrue(result.HasField('participant'),result)
        finally:peer.close()
    def service_domains(self):
        return [grants.Domain(kind=grants.WORKSPACE,id='one')]
    def write(self,name,message):
        path=self.root/(name+'.textproto');path.write_text(text_format.MessageToString(message),encoding='utf-8');path.chmod(0o600);return path
    def start_broker(self,relay=None,participate=True,factory=BrokerProcess,bindings=None,query_relay=None):
        query=self.root/'query.textproto';query.write_bytes(self.config_file(endpoint_port=query_relay.port if query_relay else None).read_bytes());query.chmod(0o600)
        service=self.root/'service.textproto';service.write_bytes(self.config_file(endpoint_port=relay.port if relay else None,io_timeout_ms=3000).read_bytes());service.chmod(0o600)
        participant=self.write('participant',issuer.AuthorityParticipantRuntimeConfig(protocol_version=1,
            issuer_client_config=str(service),private_key_path=str(self.signer),participant_id=self.definition.id,registered_revision=getattr(self,'service_revision',1)))
        config=broker.WorkspaceAuthorityConfig(protocol_version=1,issuer_client_config=str(query),
            bindings=bindings if bindings is not None else [broker.WorkspaceAuthorityBinding(workspace='one',authority_directory=str(self.directory))])
        # Old binaries have no participant field. This permits an unchanged-source counterexample.
        if participate and 'participant_runtime_config' in config.DESCRIPTOR.fields_by_name:config.participant_runtime_config=str(participant)
        elif relay:config.issuer_client_config=str(service)
        self.broker=factory(self,self.execution,self.write('broker',config));return self.broker
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
    def launch_application(self, operation_id='run', minimum_pids=5):
        principal=self.principal();actor='identity:'+principal.issuer_id.hex()+':'+principal.actor_id.hex()
        result=self.broker.request(launch=broker.LaunchRequest(workspace='one',actor=actor,principal=principal,
            command=core.ProcessLaunchRequest(operation_id=operation_id,profile='development',argv=['/usr/bin/python3','-c',RUN])))
        self.assertTrue(result.HasField('launched'),result)
        self.application=result.launched.process_id
        self.until(lambda:(self.project/'heartbeat').exists(),bool)
        pids=self.until(self.descendants,lambda value:len(value)>=minimum_pids)
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
        relay=HeldRevocationRelay(self);item=self.start_broker(relay);item.call('system.hello');poll,count=self.launch_application()
        self.revoke_now();self.assertTrue(relay.held.wait(.3));self.assertFalse(relay.delivered.is_set())
        pending=self.barrier();before=(self.project/'heartbeat').read_text();time.sleep(.08)
        after=(self.project/'heartbeat').read_text();self.assertNotEqual(before,after);self.assertEqual(poll.poll(0),[])
        self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False))
        relay.release.set();self.assertTrue(relay.delivered.wait(.3))
        completed=self.until(self.barrier,lambda value:value.complete);self.assertEqual(completed.participant_count,1)
        self.assertEqual(len(poll.poll(0)),count,'Issuer completion preceded independently witnessed process exit')
        item.close();self.assertEqual(item.proc.returncode,0);self.assertEqual(self.activations(),[(1,1,0)])
    def pin_import(self, item, name):
        repository=GitRepo(self.root/name)
        fifo=repository.path/'.git'/'observer-config'
        os.mkfifo(fifo,0o600)
        config_path=repository.path/'.git'/'config'
        original_config=config_path.read_bytes()
        with config_path.open('a') as config:
            config.write('\n[include]\n\tpath = '+str(fifo)+'\n')
        item.sequence+=1
        request=make_request('git.repo.import',dict(id=name,path=str(repository.path)))
        request.request_id=item.sequence
        item.stream.sendall(frame(broker.Request(protocol_version=1,
            request_id=item.sequence,management=request)))
        deadline=time.monotonic()+3
        # Opening the writer proves the real Git reader has reached this FIFO.
        # Keep it open and empty so only the Git wait observer can reconcile.
        while True:
            try: writer=os.open(fifo,os.O_WRONLY|os.O_NONBLOCK|os.O_CLOEXEC);break
            except OSError as error:
                if error.errno!=errno.ENXIO:raise
                self.assertLess(time.monotonic(),deadline,'Git did not reach the pinned config read')
                time.sleep(.005)
        def close_writer():
            nonlocal writer
            if writer is not None:
                # Later Git commands must not enter the one-shot pin again.
                config_path.write_bytes(original_config)
                os.close(writer);writer=None
        self.addCleanup(close_writer)
        git_pids=[];todo=[item.proc.pid]
        while todo:
            parent=todo.pop()
            try: children=Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
            except FileNotFoundError:continue
            for child in children:
                todo.append(int(child))
                try: argv=Path(f'/proc/{child}/cmdline').read_bytes().split(bytes([0]))
                except FileNotFoundError:continue
                if argv[0]==b'/usr/bin/git' and b'--absolute-git-dir' in argv:git_pids.append(int(child))
        self.assertEqual(len(git_pids),1,'The pinned reader must be the production Git worker')
        git_fd=os.pidfd_open(git_pids[0]);self.addCleanup(os.close,git_fd)
        self.assertFalse(select.select([git_fd,item.stream],[],[],0)[0],
                         'Pinned Git exited or request completed before observer revocation')
        return close_writer,git_fd,item.sequence

    def test_failed_revoked_row_commit_keeps_issuer_barrier_pending(self):
        relay=HeldRevocationRelay(self);item=self.start_broker(relay);item.call('system.hello')
        # An otherwise identical FIFO-backed import succeeds when its reader is released.
        release,_,sequence=self.pin_import(item,'control-import')
        release()
        reply=receive(item.stream.recv,item.stream,broker.Reply)
        self.assertEqual(reply.request_id,sequence)
        self.assertTrue(reply.management.HasField('repository'),reply)
        self.launch_application('first-run')
        first=self.application
        poll,count=self.launch_application('second-run',minimum_pids=10)
        applications={first,self.application}
        self.assertGreaterEqual(count,10,'Both independent application trees must be witnessed')
        release,git_fd,_=self.pin_import(item,'failed-import')
        with sqlite3.connect(self.execution/'execution.db') as db:
            self.assertEqual(set(db.execute("SELECT id,state FROM processes")),
                             {(value,'running') for value in applications})
            # The second update fails after the first row changed within the batch.
            # A checked transaction must roll that first update back too.
            db.execute("CREATE TRIGGER fail_revoked_process BEFORE UPDATE OF state ON processes "
                       "WHEN NEW.state='revoked' AND "
                       "(SELECT count(*) FROM processes WHERE state='revoked')=1 "
                       "BEGIN SELECT RAISE(ABORT,'revocation persistence fault'); END")
        self.revoke_now();self.assertTrue(relay.held.wait(.3))
        self.assertEqual((self.activations(),self.barrier().complete), ([(1,0,1)],False))
        relay.release.set();self.assertTrue(relay.delivered.wait(.5))
        deadline=time.monotonic()+8
        while True:
            pending=self.barrier()
            self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False),
                             'Observer failure was caught by dispatch and later acknowledged')
            self.assertEqual(len(pending.obligations),1,pending)
            self.assertEqual((pending.obligations[0].applied_revision,pending.obligations[0].complete),(0,False))
            if item.proc.poll() is not None:break
            self.assertLess(time.monotonic(),deadline,'Broker did not fail closed after observer persistence failure')
            time.sleep(.01)
        # RetainedBrokerChild reports workspaced, not its successful scope-owner wrapper.
        self.assertEqual(item.proc.returncode,2)
        self.assertEqual(len(poll.poll(0)),count,'Failure preceded independently witnessed application drain')
        self.assertTrue(select.select([git_fd],[],[],0)[0],'Pinned Git worker survived broker failure')
        self.assertIn('Execution registry write or read failed',Path(item.log.name).read_text())
        with sqlite3.connect(self.execution/'execution.db') as db:
            self.assertEqual(set(db.execute("SELECT id,state FROM processes")),
                             {(value,'running') for value in applications},'Batch failure did not roll back every row')
        final=self.barrier()
        self.assertEqual((final.participant_count,final.pending_count,final.complete),(1,1,False))
        self.assertEqual(len(final.obligations),1,final)
        self.assertEqual((final.obligations[0].applied_revision,final.obligations[0].complete),(0,False))
        self.assertEqual(self.activations(),[(1,0,1)])
        release();item.close()

    def test_failed_revoked_row_commit_during_blocked_wait_rolls_back_every_application(self):
        relay=HeldRevocationRelay(self);item=self.start_broker(relay);item.call('system.hello')
        self.launch_application('first-run');first=self.application
        poll,count=self.launch_application('second-run',minimum_pids=10)
        applications={first,self.application}
        self.assertGreaterEqual(count,10,'Both independent application trees must be witnessed')
        with sqlite3.connect(self.execution/'execution.db') as db:
            self.assertEqual(set(db.execute("SELECT id,state FROM processes")),
                             {(value,'running') for value in applications})
            db.execute("CREATE TRIGGER fail_revoked_process BEFORE UPDATE OF state ON processes "
                       "WHEN NEW.state='revoked' AND "
                       "(SELECT count(*) FROM processes WHERE state='revoked')=1 "
                       "BEGIN SELECT RAISE(ABORT,'revocation persistence fault'); END")
        item.sequence+=1
        request=make_request('process.wait',dict(id=first,timeout_seconds=5));request.request_id=item.sequence
        item.stream.sendall(frame(broker.Request(protocol_version=1,request_id=item.sequence,management=request)))
        # This bounded production trace precedes the borrowed ApplicationProcess wait.
        # Only revoke after the real request has entered that wait, with no completed reply.
        self.until(lambda:Path(item.log.name).read_text(),lambda value:'stage=process_wait_enter ' in value)
        self.assertFalse(select.select([item.stream],[],[],0)[0],'Blocked wait returned before revocation')
        self.assertEqual(poll.poll(0),[],'Application exited before the blocked-wait revocation')
        self.revoke_now();self.assertTrue(relay.held.wait(.3))
        self.assertEqual((self.activations(),self.barrier().complete), ([(1,0,1)],False))
        relay.release.set();self.assertTrue(relay.delivered.wait(.5))
        deadline=time.monotonic()+8
        while True:
            pending=self.barrier()
            self.assertEqual((pending.participant_count,pending.pending_count,pending.complete),(1,1,False))
            self.assertEqual(len(pending.obligations),1,pending)
            self.assertEqual((pending.obligations[0].applied_revision,pending.obligations[0].complete),(0,False))
            if item.proc.poll() is not None:break
            self.assertLess(time.monotonic(),deadline,'Broker did not fail closed after wait persistence failure')
            time.sleep(.01)
        self.assertEqual(item.proc.returncode,2)
        self.assertEqual(len(poll.poll(0)),count,'Failure preceded independently witnessed application drain')
        output=Path(item.log.name).read_text()
        self.assertIn('stage=process_wait_revoked ',output,'Revocation missed the borrowed wait authority callback')
        self.assertIn('Execution registry write or read failed',output)
        with sqlite3.connect(self.execution/'execution.db') as db:
            self.assertEqual(set(db.execute("SELECT id,state FROM processes")),
                             {(value,'running') for value in applications},'Wait bypassed batch rollback')
        final=self.barrier()
        self.assertEqual((final.participant_count,final.pending_count,final.complete),(1,1,False))
        self.assertEqual(len(final.obligations),1,final)
        self.assertEqual((final.obligations[0].applied_revision,final.obligations[0].complete),(0,False))
        self.assertEqual(self.activations(),[(1,0,1)])
        item.close()

    def test_credential_revocation_preserves_actor_owned_execution(self):
        item=self.start_broker();item.call('system.hello');poll,count=self.launch_application();self.revoke_now(issuer.CREDENTIAL_TARGET)
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
