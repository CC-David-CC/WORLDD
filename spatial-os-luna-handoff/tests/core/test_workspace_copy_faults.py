"""M07 workspace-copy fault regressions.

Runtime execution is restricted to sb16 and sb16-2 through the dedicated runner.
"""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_workspace_copy_engine as engine

core=engine.core
copy=engine.copy
storage=engine.storage
authority=engine.fixtures.issuer
FAULT_RELAY=engine.SOURCE/'tests/native/storage_owner_copy_fault_relay.py'


class CopyFaultStorageBrokerProcess(engine.StorageBrokerProcess):
    def __init__(self,fixture,state,config=None):
        self.stream,child=socket.socketpair();self.sequence=0
        self.log=(fixture.root/f'copy-fault-broker-{len(fixture.brokers)}.log').open('wb')
        self.owner_log=(fixture.root/f'copy-fault-resource-{len(fixture.brokers)}.log').open('wb')
        self.storage_log=(fixture.root/f'copy-fault-storage-{len(fixture.brokers)}.log').open('wb')
        owner_end,broker_end=socket.socketpair()
        for end in (owner_end,broker_end):end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        command=[str(engine.fixtures.BROKER),'--state-dir',str(state),'--storage-fd',str(broker_end.fileno())]
        if config:command+=['--authority-config',str(config)]
        configuration=str(fixture.copy_resource_config)
        self.resources=engine.ResourceBroker(engine.fixtures.BROKER,command,child,self.log,self.owner_log,configuration,
                                             extra_fds={'--storage-fd':broker_end.fileno()})
        self.proc=self.resources.process;self.pid=self.resources.pid
        broker_end.close();child.close()
        owner_config=storage.StorageOwnerConfig.FromString(fixture.owner_configuration.read_bytes())
        owner_config.broker_pid=self.pid
        local=fixture.root/f'fault-owner-launch-{len(fixture.brokers)}.pb'
        local.write_bytes(owner_config.SerializeToString());local.chmod(0o600)
        private=fixture.operator_directory/f'fault-launch-{len(fixture.brokers)}.pb'
        fixture.operator_run(['install','-m','600',str(local),str(private)])
        code='import os,sys;os.dup2(0,3);os.set_inheritable(3,True);os.execv(sys.argv[1],sys.argv[1:])'
        owner_command=[sys.executable,'-B',str(FAULT_RELAY),'--channel-fd','3','--build',str(engine.BUILD),
            '--config',str(private),'--audit',str(fixture.operator_directory/f'copy-fault-{len(fixture.brokers)}.json'),
            '--mode',fixture.copy_fault_mode]
        self.audit_path=fixture.operator_directory/f'copy-fault-{len(fixture.brokers)}.json'
        self.owner=subprocess.Popen(['sudo','-n','env','ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
            'UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',sys.executable,'-B','-c',code,*owner_command],
            stdin=owner_end,stdout=subprocess.DEVNULL,stderr=self.storage_log)
        owner_end.close();fixture.brokers.append(self)



class WorkspaceCopyFaultTests(engine.WorkspaceCopyEngineTests):
    def setUp(self):
        self.copy_fault_mode=('hold-copy-seal' if self._testMethodName==
                              'test_actor_identity_revocation_after_real_seal_receipt_blocks_copy_publication'
                              else 'delay-copy-replies')
        replacement=patch.object(engine,'StorageBrokerProcess',CopyFaultStorageBrokerProcess)
        replacement.start()
        try:
            super().setUp()
            self.actor_a=(self.identity,self.record,self.actor_key,self.actor_public)
            self.enroll_second()
            self.actor_b=(self.second,self.second_record,self.second_key,self.second_public)
        except BaseException:
            replacement.stop()
            raise
        self.addCleanup(replacement.stop)

    def provision(self,operation):
        if operation=='create':
            definition=engine.broker.StorageVolumeDefinition()
            engine.text_format.Parse(self.definition_path.read_text(),definition)
            # Fourteen independently configured 4 MiB persistent states need
            # 56 MiB of actor quota. Keep one runtime allocation within 64 MiB.
            definition.owner_policy.default_policy.actor_state_default_bytes=64*2**20
            self.definition_path.write_text(engine.text_format.MessageToString(definition))
            inventory=self.copy_evidence/'volume.json'
            record=json.loads(inventory.read_text())
            record['definition_hex']=definition.SerializeToString(deterministic=True).hex()
            inventory.write_text(json.dumps(record,indent=2)+'\n')
        return super().provision(operation)

    def after_workspace_storage_setup(self):
        super().after_workspace_storage_setup()
        config=storage.StorageOwnerConfig.FromString(self.owner_configuration.read_bytes())
        profile=next(item for item in config.copy_destination_profiles if item.name=='copy-evaluation')
        # Nineteen independently reserved destination objects need at least
        # 76 MiB under the owner's 4 MiB per-object minimum charge.
        profile.maximum_total_bytes=128*2**20
        profile.storage_policy.total_bytes=128*2**20
        config.copy_catalog.revision+=1
        self.owner_configuration.write_bytes(config.SerializeToString(deterministic=True))
        private=self.operator_directory/'migration-copy-budget.pb'
        self.operator_run(['install','-m','600',self.owner_configuration,private])
        self.operator_run([engine.BUILD/'spatial-storage-owner','--config',private,'--migrate-copy-catalog'])

    def configure_fault_actors(self):
        actor_a,record_a,key_a,public_a=self.actor_a
        actor_b,record_b,key_b,public_b=self.actor_b
        self.identity,self.record,self.actor_key,self.actor_public=actor_b,record_b,key_b,public_b
        return actor_a,actor_b

    def test_absolute_copy_budget_expires_across_real_subtimeout_owner_replies(self):
        self.copy_fault_mode='delay-copy-replies'
        keys=tuple('app-%02d'%number for number in range(14))
        self.configure_state_writers(keys=keys)
        world=self.workspace(roots=self.collection_roots,registry=True)
        peer=world.peers[0];self.select(peer)
        for key in keys:
            process=self.launch_configured(peer,key,'deadline-'+key)
            current_paths=self.configured_paths()
            self.assertIn(key,current_paths)
            self.wait_state_writes({key:current_paths[key]})
            (self.project/('stop-'+key)).touch()
            self.wait_job(world,process)
            (self.project/('stop-'+key)).unlink()
        paths=self.configured_paths()
        self.assertEqual(set(paths),set(keys))
        with sqlite3.connect(self.execution/'execution.db') as database:
            process_states=database.execute('SELECT state,count(*) FROM processes GROUP BY state').fetchall()
        self.assertEqual(dict(process_states),{'exited':14},
            'All fourteen source applications must leave only inactive process rows before copy admission')
        owner_config=storage.StorageOwnerConfig.FromString(self.owner_configuration.read_bytes())
        self.assertEqual(owner_config.default_policy.total_bytes,64*2**20)
        self.assertGreaterEqual(owner_config.default_policy.actor_state_default_bytes,56*2**20)
        request=self.request_copy(peer,self.selected_classes)
        accepted=peer.act(workspace_copy=request)
        self.assertTrue(accepted.HasField('action'),accepted)
        self.assertEqual(accepted.action.copy_status.phase,copy.REQUESTED,accepted)
        with sqlite3.connect(self.execution/'execution.db') as database:
            raw=database.execute('SELECT authorization_binding FROM copy_operations WHERE operation_id=?',
                                 (request.operation_id,)).fetchone()[0]
        binding=storage.WorkspaceCopyAuthorizationBinding.FromString(raw)
        self.assertEqual(len(binding.state_companions),14)
        self.assertEqual({entry.data_class for entry in binding.owner_catalog.entries},set(self.selected_classes))
        destination_profile=binding.owner_catalog.destination_profile
        self.assertEqual(destination_profile.maximum_total_bytes,128*2**20)
        self.assertEqual(destination_profile.storage_policy.total_bytes,128*2**20)
        self.assertGreaterEqual(destination_profile.storage_policy.total_bytes,19*4*2**20)
        boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.assertEqual(binding.boot_id,boot)
        self.assertGreater(binding.accepted_monotonic_ns,0)
        detected=None;final=None;observation_started=None;observation_elapsed=None
        watchdog=binding.accepted_monotonic_ns+310_000_000_000
        while time.monotonic_ns()<watchdog:
            request_started=time.monotonic_ns()
            reply=peer.act(workspace_copy_status=copy.WorkspaceCopyStatusRequest(
                protocol_version=1,operation_id=request.operation_id))
            received=time.monotonic_ns()
            self.assertTrue(reply.HasField('action'),reply)
            final=reply.action.copy_status
            if final.phase in (copy.ABORTED,copy.UNCERTAIN):
                detected=received;observation_started=request_started;observation_elapsed=received-request_started;break
            time.sleep(.02)
        self.assertIsNotNone(detected,'Copy did not reach a durable terminal error before the watchdog')
        elapsed=detected-binding.accepted_monotonic_ns
        self.assertGreaterEqual(elapsed,300_000_000_000)
        self.assertLess(elapsed,306_000_000_000)
        self.assertLess(observation_elapsed,5_000_000_000,'Terminal status observation exceeded its actor request budget')
        self.assertIn(final.phase,(copy.ABORTED,copy.UNCERTAIN),final)
        if final.phase==copy.ABORTED:self.assertEqual(final.error_code,copy.COPY_UNAVAILABLE,final)
        else:self.assertEqual(final.error_code,copy.COPY_UNCERTAIN,final)
        self.assertFalse(final.HasField('authorized_destination_workspace_id'),final)
        with sqlite3.connect(self.execution/'execution.db') as database:
            phase,error=database.execute('SELECT phase,error FROM copy_operations WHERE operation_id=?',
                                         (request.operation_id,)).fetchone()
            workspace_count=database.execute('SELECT count(*) FROM workspaces').fetchone()[0]
            published=database.execute("SELECT count(*) FROM copy_effects WHERE operation_id=? AND kind='publication'",
                                       (request.operation_id,)).fetchone()[0]
            allocation_row=database.execute("SELECT receipt FROM copy_effects WHERE operation_id=? AND kind='destination-allocation' AND state=1",
                                            (request.operation_id,)).fetchone()
        self.assertEqual(phase,final.phase)
        self.assertEqual(workspace_count,1,'Deadline failure must not publish a destination workspace')
        self.assertEqual(published,0,'Deadline failure must not record destination publication')
        self.assertIsNotNone(allocation_row,'The 14-companion control copy did not reach destination allocation')
        allocation=storage.StorageCopyDestinationReceipt.FromString(allocation_row[0])
        self.assertEqual(allocation.state,storage.StorageCopyDestinationReceipt.ALLOCATED)
        self.assertEqual(len(allocation.state_companions),14)
        self.assertTrue(all(item.maximum_bytes==4*2**20 for item in allocation.state_companions))
        self.worlds[-1].close();self.broker.close()
        audit=json.loads(self.operator_run(['cat',self.broker.audit_path]))
        responses=audit['copy_responses']
        self.assertGreaterEqual(len(responses),2,audit)
        self.assertTrue(all(item['elapsed_seconds']>=4.1 for item in responses),audit)
        self.assertTrue(all(item['elapsed_seconds']<5 for item in responses),audit)
        self.assertTrue(all(item['reply_sha256']==item['forwarded_reply_sha256'] for item in responses),audit)
        diagnostic=Path(self.broker.log.name).read_text(errors='replace')
        timeout_marker='workspace copy failed operation='+request.operation_id.hex()
        self.assertIn(timeout_marker,diagnostic)
        self.assertIn('reason=Workspace copy absolute operation budget expired',diagnostic)
        self.assertEqual(audit['mutations'],[])
        self.assertEqual(audit['owner_exit'],0)
        self.assertEqual(audit['broker_exit'],0)
        (self.copy_evidence/'absolute-deadline.json').write_text(json.dumps({
            'accepted_monotonic_ns':binding.accepted_monotonic_ns,'boot_id':binding.boot_id,
            'detection_monotonic_ns':detected,'accepted_to_detection_ns':elapsed,
            'terminal_status_request_started_ns':observation_started,
            'terminal_status_request_elapsed_ns':observation_elapsed,
            'detection_after_deadline_ns':elapsed-300_000_000_000,
            'phase':final.phase,'error_code':final.error_code,'copy_responses':responses,
            'fragmented_owner_reply_seconds':sum(item['elapsed_seconds'] for item in responses),
            'source_workspace_total_bytes':owner_config.default_policy.total_bytes,
            'source_actor_state_default_bytes':owner_config.default_policy.actor_state_default_bytes,
            'destination_workspace_total_bytes':destination_profile.storage_policy.total_bytes,
            'destination_profile_maximum_total_bytes':destination_profile.maximum_total_bytes,
            'control_destination_allocation_sha256':__import__('hashlib').sha256(allocation_row[0]).hexdigest(),
            'control_destination_state_quota_bytes':sum(item.maximum_bytes for item in allocation.state_companions),
            'absolute_timeout_diagnostic_sha256':__import__('hashlib').sha256(diagnostic.encode()).hexdigest(),
            'cleanup_timing':'recorded by fixture lifecycle; no new bounded-cleanup contract'},sort_keys=True,indent=2)+'\n')

    def test_actor_identity_revocation_after_real_seal_receipt_blocks_copy_publication(self):
        actor_a,actor_b=self.configure_fault_actors()
        self.copy_fault_mode='hold-copy-seal'
        self.configure_state_writers(keys=('editor',))
        # Both enrolled actors hold independent workspace-copy authority.
        self.workspace(roots=lambda:[
            self.workspace_terms(actor_a,rights=[*engine.fixtures.RIGHTS,engine.grants.WORKSPACE_COPY_PROJECT,
                engine.grants.WORKSPACE_COPY_GIT,engine.grants.WORKSPACE_COPY_DEFINITION,
                engine.grants.WORKSPACE_COPY_STATE,engine.grants.WORKSPACE_COPY_ENVIRONMENT]),
            self.workspace_terms(actor_b,rights=[*engine.fixtures.RIGHTS,engine.grants.WORKSPACE_COPY_PROJECT,
                engine.grants.WORKSPACE_COPY_GIT,engine.grants.WORKSPACE_COPY_DEFINITION,
                engine.grants.WORKSPACE_COPY_STATE,engine.grants.WORKSPACE_COPY_ENVIRONMENT],depth=0)],
            registry=True,attach_world=False)
        session=engine.fixtures.session
        state=self.root/'copy-fault-two-actor-world'
        initial=self.world(admitted=False,state=state)
        self.world_info=initial.management('world.info');initial.close()
        directory=self.root/'copy-fault-world-authority';directory.mkdir(mode=0o700)
        seed=session.WorldAuthoritySeed(protocol_version=1,authority_directory=str(directory),
            roots=[self.terms(claims=actor_a),self.terms(claims=actor_b)])
        seed_path=self.root/'copy-fault-world-seed.textproto'
        seed_path.write_text(engine.text_format.MessageToString(seed));seed_path.chmod(0o600)
        initialized=subprocess.run([str(engine.fixtures.WORLD),'--state-dir',str(state),
            '--init-authority',str(seed_path)],capture_output=True,timeout=8)
        self.assertEqual(initialized.returncode,0,initialized.stderr)
        config=session.WorldIdentityConfig(protocol_version=1,issuer_client_config=str(self.config_file()),
            admission=session.EXPLICIT_ACTORS,authority_directory=str(directory),
            admitted_actor_ids=[actor_a.actor_id,actor_b.actor_id])
        self.configure_world_participant(config,state)
        config_path=self.root/'copy-fault-two-actor-world.textproto'
        config_path.write_text(engine.text_format.MessageToString(config));config_path.chmod(0o600)
        runtime=engine.core.RuntimeConfig()
        engine.text_format.Parse((engine.fixtures.WORLD.parent/'runtime.textproto').read_text(),runtime)
        runtime.input_timeout=10
        runtime_path=self.root/'copy-fault-two-actor-runtime.textproto'
        runtime_path.write_text(engine.text_format.MessageToString(runtime))
        world=engine.fixtures.WorldProcess(self,config_path,state,runtime_config=runtime_path,broker=self.broker)
        self.worlds.append(world);world.management('system.hello')
        world.state_path=state;world.config_path=config_path;world.runtime_path=runtime_path
        world.management('workspace.available',id='one',available=True)
        peer_a,peer_b=world.peers
        self.assertTrue(self.authenticate(peer_a,record=self.actor_a[1],key=self.actor_a[2],public=self.actor_a[3]).HasField('opened'))
        self.assertTrue(self.authenticate(peer_b,record=self.actor_b[1],key=self.actor_b[2],public=self.actor_b[3]).HasField('opened'))
        self.select(peer_a);self.select(peer_b)
        source=self.launch_configured(peer_a,'editor','fault-source-app')
        paths=self.configured_paths();self.wait_state_writes(paths)
        payloads={}
        for pid in self.descendants():
            try:
                relative=Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1]
                if Path(relative).name=='payload':payloads[pid]=self.start_time(pid)
            except (FileNotFoundError,IndexError):pass
        self.assertTrue(payloads,'Source application has no independently witnessed payload process')
        request=self.request_copy(peer_b,self.selected_classes)
        begun=peer_b.act(workspace_copy=request)
        self.assertTrue(begun.HasField('action'),begun)
        self.assertEqual(begun.action.copy_status.phase,copy.REQUESTED,begun)
        statuses=[]
        stop_worker=threading.Event()
        def advance_copy():
            while not stop_worker.is_set():
                try:
                    result=peer_b.act(workspace_copy_status=copy.WorkspaceCopyStatusRequest(
                        protocol_version=1,operation_id=request.operation_id))
                    statuses.append(result)
                    if result.HasField('action') and result.action.copy_status.phase in (copy.ABORTED,copy.UNCERTAIN,copy.PUBLISHED):
                        return
                except BaseException as error:
                    statuses.append(error);return
        worker=threading.Thread(target=advance_copy,name='copy-fault-status-poll',daemon=False)
        worker.start()
        held=self.broker.audit_path.with_suffix('.seal-held')
        release=self.broker.audit_path.with_suffix('.seal-release')
        def stop_copy_poll():
            stop_worker.set()
            if subprocess.run(['sudo','-n','test','-e',str(held)],capture_output=True).returncode==0:
                self.operator_run(['touch',release])
            worker.join(timeout=35)
            if worker.is_alive():raise AssertionError('Copy status polling thread did not stop')
        self.addCleanup(stop_copy_poll)
        held_deadline=time.monotonic()+90
        while time.monotonic()<held_deadline:
            found=subprocess.run(['sudo','-n','test','-e',str(held)],capture_output=True)
            if found.returncode==0:break
            time.sleep(.05)
        self.assertEqual(subprocess.run(['sudo','-n','test','-e',str(held)]).returncode,0,
            'Real owner SEAL receipt did not reach the deterministic relay gate')
        held_record=json.loads(self.operator_run(['cat',held]))
        held_observed=time.monotonic_ns()
        # A fresh issuer connection revokes B while its exact valid receipt is held.
        revoke_started=time.monotonic_ns()
        issuer_peer=None;witness=None
        try:
            issuer_peer=self.connect()
            operation=authority.IssuerOperation(identity_revoke=authority.IdentityRevoke(
                target=authority.IdentityReference(kind=authority.ACTOR_TARGET,id=actor_b.actor_id),
                expected_security_revision=0,reason='copy actor revocation regression'))
            witness=self.execute(issuer_peer,self.challenge(issuer_peer,operation))
        finally:
            try:
                if issuer_peer is not None:issuer_peer.close()
            finally:
                self.operator_run(['touch',release])
        release_sent=time.monotonic_ns()
        self.assertIsNotNone(witness,'Fresh issuer identity revocation did not complete')
        self.assertTrue(witness.HasField('identity_status'),witness)
        self.assertTrue(witness.identity_status.actor_revoked,witness)
        self.assertGreater(witness.identity_status.security_revision,0,witness)
        self.assertLess(release_sent-revoke_started,5_000_000_000,
            'Issuer revocation and release of the exact SEAL receipt exceeded the owner I/O budget')
        worker.join(timeout=35)
        self.assertFalse(worker.is_alive(),'Status request did not return after the held receipt was forwarded')
        stop_worker.set()
        # Actor A remains authorized. Its successful launch proves the source barrier was removed.
        probe=self.launch_process(peer_a,"from pathlib import Path;Path('post-revoke-launch').write_text('ok')",
                                  operation='post-revocation-barrier-probe')
        self.wait_job(world,probe)
        for pid,start in payloads.items():self.assertEqual(self.start_time(pid),start,'Source PID/start identity changed')
        for pid in payloads:
            group=Path('/sys/fs/cgroup')/Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1].lstrip('/')
            self.assertIn('frozen 0',(group/'cgroup.events').read_text())
        with sqlite3.connect(self.execution/'execution.db') as database:
            phase,error,destination=database.execute(
                'SELECT phase,error,destination FROM copy_operations WHERE operation_id=?',(request.operation_id,)).fetchone()
            count=database.execute('SELECT count(*) FROM workspaces').fetchone()[0]
            published=database.execute("SELECT count(*) FROM copy_effects WHERE operation_id=? AND kind='publication'",
                                       (request.operation_id,)).fetchone()[0]
            release_row=database.execute("SELECT receipt FROM copy_effects WHERE operation_id=? AND kind='quiescence-release' AND state=1",
                                         (request.operation_id,)).fetchone()
        self.assertEqual(phase,copy.ABORTED)
        self.assertEqual(error,copy.COPY_FORBIDDEN)
        self.assertEqual(destination,'')
        self.assertEqual(count,1)
        self.assertEqual(published,0)
        self.assertIsNotNone(release_row,'Copy did not persist its quiescence release receipt')
        thaw=storage.WorkspaceQuiescenceReceipt.FromString(release_row[0])
        self.assertTrue(thaw.released)
        self.assertFalse(thaw.launch_barrier)
        self.assertFalse(thaw.filesystem_mutator_barrier)
        self.assertTrue(thaw.applications)
        self.assertTrue(all(not app.frozen for app in thaw.applications))
        self.worlds[-1].close();self.broker.close()
        audit=json.loads(self.operator_run(['cat',self.broker.audit_path]))
        seals=[item for item in audit['copy_responses'] if item['method']=='copy_stage_seal']
        self.assertEqual(len(seals),1,audit)
        self.assertEqual(seals[0]['reply_sha256'],held_record['held_receipt_sha256'])
        self.assertLess(seals[0]['elapsed_seconds'],5,'SEAL request exceeded the existing owner I/O budget')
        self.assertEqual(seals[0]['held_receipt_sha256'],held_record['held_receipt_sha256'])
        seal_at=audit['requests'].index('copy_stage_seal')
        later=audit['requests'][seal_at+1:]
        self.assertFalse(set(later)&{'copy_stage_begin','copy_stage_start','copy_stage_seal','copy_destination_publish'},audit)
        self.assertNotIn('copy_destination_publish',audit['requests'])
        (self.copy_evidence/'issuer-revocation.json').write_text(json.dumps({
            'actor_id_sha256':__import__('hashlib').sha256(actor_b.actor_id).hexdigest(),
            'issuer_security_revision':witness.identity_status.security_revision,
            'seal_observed_monotonic_ns':held_observed,'revoke_started_monotonic_ns':revoke_started,
            'receipt_release_monotonic_ns':release_sent,
            'revoke_and_release_elapsed_ns':release_sent-revoke_started,
            'held_seal_reply_sha256':held_record['held_receipt_sha256'],
            'phase':phase,'error_code':error,'owner_calls_after_seal':later,
            'payload_pid_start_ticks':payloads,'thaw_receipt_sha256':__import__('hashlib').sha256(release_row[0]).hexdigest(),
            'copy_responses':audit['copy_responses']},sort_keys=True,indent=2)+'\n')


def load_tests(loader,tests,pattern):
    names=('test_absolute_copy_budget_expires_across_real_subtimeout_owner_replies',
           'test_actor_identity_revocation_after_real_seal_receipt_blocks_copy_publication')
    suite=unittest.TestSuite(WorkspaceCopyFaultTests(name) for name in names)
    assert len(suite._tests)==2
    return suite

if __name__=='__main__':unittest.main()
