"""M07 workspace-copy fault regressions.

Runtime execution is restricted to sb16 and sb16-2 through the dedicated runner.
"""
import json
import hashlib
from contextlib import closing
import signal
import os
import select
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
SUPERVISED_BROKER_KILL=engine.SOURCE/'tests/native/operator_kill_supervised_broker.py'


def is_revoked_copy_status_denial(code,message,*,unauthorized,forbidden):
    """Match only source-defined typed denials after independently proven revocation."""
    return (code,message) in {
        (forbidden,'Workspace copy status authority is denied'),
        (unauthorized,'Issuer revocation invalidated the in-flight broker result'),
        (unauthorized,'Actor credential is revoked'),
        (unauthorized,'Actor identity is inactive'),
        (unauthorized,'Channel has no authenticated actor'),
        (unauthorized,'World session was revoked'),
        # identity_sessions.cpp841-846 preserves an underlying FORBIDDEN denial,
        # or uses UNAUTHORIZED when cancellation supersedes a successful result.
        (unauthorized,'World session changed during operation'),
        (forbidden,'World session changed during operation'),
    }


def read_copy_terminal_before_deadline(database_path,operation_id,deadline_ns,busy_audit):
    """Read one committed journal row within the existing observation deadline."""
    while True:
        started=time.monotonic_ns()
        remaining=(deadline_ns-started)/1e9
        if remaining<=0:raise TimeoutError('Copy journal observation exceeded its completion deadline')
        try:
            with closing(sqlite3.connect('file:'+str(database_path)+'?mode=ro',uri=True,
                    timeout=min(.1,remaining))) as database:
                row=database.execute('SELECT phase,error,destination FROM copy_operations WHERE operation_id=?',
                                     (operation_id,)).fetchone()
            if time.monotonic_ns()>deadline_ns:
                raise TimeoutError('Copy journal observation exceeded its completion deadline')
            return row
        except sqlite3.OperationalError as error:
            observed=time.monotonic_ns()
            record=dict(started_monotonic_ns=started,returned_monotonic_ns=observed,
                        sqlite_errorcode=getattr(error,'sqlite_errorcode',None),
                        sqlite_errorname=getattr(error,'sqlite_errorname',None))
            busy_audit['last_error']=record
            if record['sqlite_errorcode']!=sqlite3.SQLITE_BUSY:raise
            busy_audit['count']+=1
            if busy_audit['first'] is None:busy_audit['first']=record
            busy_audit['last']=record
            time.sleep(min(.05,max(0,(deadline_ns-time.monotonic_ns())/1e9)))


class CopyFaultStorageBrokerProcess(engine.StorageBrokerProcess):
    def __init__(self,fixture,state,config=None):
        self.fixture=fixture
        self.stream,child=socket.socketpair();self.sequence=0
        self.log=(fixture.root/f'copy-fault-broker-{len(fixture.brokers)}.log').open('wb')
        self.owner_log=(fixture.root/f'copy-fault-resource-{len(fixture.brokers)}.log').open('wb')
        self.storage_log=(fixture.root/f'copy-fault-storage-{len(fixture.brokers)}.log').open('wb')
        owner_end,broker_end=socket.socketpair()
        for end in (owner_end,broker_end):end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        supervised=config is not None and (fixture.copy_fault_mode=='drop-publication-ack' or hasattr(fixture,'lostack_supervisors'))
        self.partial_ready=not supervised
        if supervised:
            def cleanup_partial_supervised_broker():
                if self.partial_ready:return
                failures=[]
                for stream in (self.stream,child,owner_end,broker_end):stream.close()
                try:
                    if getattr(self,'resources',None) is not None:self.resources.close()
                except BaseException as error:failures.append('Supervisor cleanup: '+type(error).__name__)
                owner=getattr(self,'owner',None)
                if owner is not None:
                    try:owner.wait(timeout=10)
                    except BaseException as error:failures.append('Root owner cleanup: '+type(error).__name__)
                for log in (self.log,self.owner_log,self.storage_log):
                    if not log.closed:log.flush();log.close()
                    if Path(log.name).exists():(fixture.copy_evidence/Path(log.name).name).write_bytes(Path(log.name).read_bytes())
                (fixture.copy_evidence/'lost-publication-partial-broker-cleanup.json').write_text(json.dumps(
                    dict(failures=failures,owner_exit=None if owner is None else owner.poll()),sort_keys=True,indent=2)+'\n')
                if failures:raise AssertionError('; '.join(failures))
            fixture.addCleanup(cleanup_partial_supervised_broker)
        command=[str(engine.fixtures.BROKER),'--state-dir',str(state),'--storage-fd',str(broker_end.fileno())]
        if config:command+=['--authority-config',str(config)]
        configuration=str(fixture.copy_resource_config)
        if supervised:
            # Both authority generations must have real crash ownership before the lost-ACK fault.
            sys.path.insert(0,str(engine.SOURCE/'tests/native'))
            from copy_fault_supervised_world import SupervisedBrokerResources
            self.resources=SupervisedBrokerResources(fixture,engine.fixtures.BROKER,command,child,self.log,self.owner_log,
                configuration,extra_fds={'--storage-fd':broker_end.fileno()})
        else:
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
        owner_end.close();self.initialize_transport(world_channel_opt_in=False);fixture.brokers.append(self)
        self.partial_ready=True


    def close(self):
        try:
            super().close()
        finally:
            # Keep every relay audit and service diagnostic before volume cleanup,
            # including failed assertions and setup attempts.
            fixture=self.fixture
            if self.audit_path is not None:
                result=subprocess.run(['sudo','-n','cat',str(self.audit_path)],capture_output=True,timeout=5)
                if result.returncode==0:
                    (fixture.copy_evidence/self.audit_path.name).write_bytes(result.stdout)
            for log in (self.log,self.owner_log,self.storage_log):
                if not log.closed:log.flush()
                path=Path(log.name)
                if path.exists():(fixture.copy_evidence/path.name).write_bytes(path.read_bytes())



class WorkspaceCopyFaultTests(engine.WorkspaceCopyEngineTests):
    # Keep the calibrated engine fixture values and override only this fault scenario.
    SOURCE_STATE_BYTES_BY_SCENARIO={
        **engine.WorkspaceCopyEngineTests.SOURCE_STATE_BYTES_BY_SCENARIO,
        'test_actor_identity_revocation_after_real_seal_receipt_blocks_copy_publication':64*2**20}
    SOURCE_POLICY_BYTES_BY_SCENARIO={
        **engine.WorkspaceCopyEngineTests.SOURCE_POLICY_BYTES_BY_SCENARIO,
        'test_actor_identity_revocation_after_real_seal_receipt_blocks_copy_publication':dict(
            actor_state_bytes=256*2**20,workspace_bytes=256*2**20,destination_copy_source_bytes=4*2**20)}
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
            source_policy=self.SOURCE_POLICY_BYTES_BY_SCENARIO.get(self._testMethodName,{})
            actor_state_bytes=source_policy.get('actor_state_bytes',64*2**20)
            definition.owner_policy.default_policy.actor_state_default_bytes=actor_state_bytes
            self.definition_path.write_text(engine.text_format.MessageToString(definition))
            inventory=self.copy_evidence/'volume.json'
            record=json.loads(inventory.read_text())
            record['source_state_ceiling_bytes']=self.SOURCE_STATE_BYTES_BY_SCENARIO.get(self._testMethodName,4*2**20)
            record['source_actor_ceiling_bytes']=actor_state_bytes
            record['source_workspace_ceiling_bytes']=definition.owner_policy.default_policy.total_bytes
            record['definition_hex']=definition.SerializeToString(deterministic=True).hex()
            inventory.write_text(json.dumps(record,indent=2)+'\n')
        try:
            return super().provision(operation)
        except BaseException as error:
            if operation=='unmount' and 'target is busy' in str(error):
                try:self.retain_busy_volume_refs()
                except BaseException as diagnostic_error:
                    print('Busy volume snapshot failed: '+type(diagnostic_error).__name__,file=sys.stderr)
            raise

    def retain_busy_volume_refs(self):
        # Passive fixture-only evidence. Preserve the unmount error and existing recovery.
        until=time.monotonic()+1
        volume=self.execution_override
        record=dict(stage='after-EBUSY-before-outer-recovery',monotonic_ns=time.monotonic_ns(),
                    fixture_pid=os.getpid(),volume=str(volume),references=[],known_processes=[],scopes=[],errors=[])
        own=Path('/proc/self')
        try:
            links=[own/'cwd',own/'root']
            for index,path in enumerate((own/'fd').iterdir()):
                if index>=1024 or time.monotonic()>=until:break
                links.append(path)
            for path in links:
                if time.monotonic()>=until:break
                try:
                    target=os.readlink(path)
                    relative=Path(target.removesuffix(' (deleted)')).relative_to(volume)
                    record['references'].append(dict(kind=path.parent.name if path.parent.name=='fd' else path.name,
                        fd=int(path.name) if path.parent.name=='fd' else None,target_relative=str(relative),
                        deleted=target.endswith(' (deleted)')))
                except ValueError:pass  # Never record unrelated paths.
                except OSError as error:record['errors'].append(type(error).__name__)
        except OSError as error:record['errors'].append(type(error).__name__)
        known={os.getpid()}
        for item in [*self.worlds,*self.brokers][:16]:
            resources=getattr(item,'resources',None)
            for process in (getattr(item,'proc',None),getattr(item,'owner',None),getattr(resources,'owner',None)):
                pid=getattr(process,'pid',None)
                if isinstance(pid,int) and pid>0:known.add(pid)
            scope=getattr(resources,'root',None)
            if isinstance(scope,Path) and scope.is_relative_to('/sys/fs/cgroup'):
                record['scopes'].append(dict(path=str(scope),exists=scope.exists()))
        for pid in sorted(known):
            if time.monotonic()>=until:break
            process=Path('/proc')/str(pid);row=dict(pid=pid)
            try:
                stat=process.joinpath('stat').read_text().rsplit(')',1)[1].split()
                row.update(state=stat[0],start_ticks=int(stat[19]))
            except OSError as error:row['unavailable']=type(error).__name__
            record['known_processes'].append(row)
        record['budget_exhausted']=time.monotonic()>=until
        (self.copy_evidence/'busy-volume-refs.json').write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')

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
        with closing(sqlite3.connect(self.execution/'execution.db')) as database, database:
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
        with closing(sqlite3.connect(self.execution/'execution.db')) as database, database:
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
        with closing(sqlite3.connect(self.execution/'execution.db')) as database, database:
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
            admission=session.EXPLICIT_ACTORS,authority_directory=str(directory))
        # Persistent authority admits these actors through the existing WORLD_ENTER
        # roots. Static admission lists cannot be mixed with that authority mode.
        self.assertEqual(config.admission,session.EXPLICIT_ACTORS)
        self.assertEqual(config.authority_directory,seed.authority_directory)
        self.assertFalse(config.admitted_actor_ids);self.assertFalse(config.actor_grants)
        self.assertEqual(len(seed.roots),2)
        self.assertEqual({term.subject.SerializeToString() for term in seed.roots},
            {self.principal(actor_a).SerializeToString(),self.principal(actor_b).SerializeToString()})
        for term in seed.roots:
            self.assertEqual(term.scope.domain.kind,engine.grants.WORLD)
            self.assertEqual(term.scope.domain.id,self.world_info['world_id'])
            self.assertFalse(term.scope.components)
            self.assertIn(engine.grants.WORLD_ENTER,term.rights)
        self.configure_world_participant(config,state)
        config_path=self.root/'copy-fault-two-actor-world.textproto'
        config_path.write_text(engine.text_format.MessageToString(config));config_path.chmod(0o600)
        runtime=engine.core.RuntimeConfig()
        engine.text_format.Parse((engine.fixtures.WORLD.parent/'runtime.textproto').read_text(),runtime)
        runtime.input_timeout=10
        runtime_path=self.root/'copy-fault-two-actor-runtime.textproto'
        runtime_path.write_text(engine.text_format.MessageToString(runtime))
        world=engine.fixtures.WorldProcess(self,config_path,state,runtime_config=runtime_path,broker=self.broker)
        world.lifecycle_evidence=self.copy_evidence/'issuer-revocation-status.json'
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
        # A typed denial is a completed actor exchange, not another copy-status phase.
        # Keep bounded summaries rather than retaining every protobuf reply.
        status_lock=threading.Lock()
        status_audit={'count':0,'first':None,'last':None,'error_counts':{},
                      'exceptions':[],'published_seen':False,'denial_validated':False,
                      'release_monotonic_ns':None,'completion_deadline_monotonic_ns':None,
                      'durable_terminal':None,
                      'durable_busy':dict(count=0,first=None,last=None,last_error=None)}
        stop_worker=threading.Event()
        def record_status(result,started):
            record={'started_monotonic_ns':started,'returned_monotonic_ns':time.monotonic_ns()}
            if result.HasField('error'):
                record.update(kind='error',code=result.error.code,message=result.error.message[:256])
            elif result.HasField('action'):
                record.update(kind='action',phase=result.action.copy_status.phase)
            else:record['kind']='unexpected-reply'
            with status_lock:
                status_audit['count']+=1
                if status_audit['first'] is None:status_audit['first']=record
                status_audit['last']=record
                if record['kind']=='error':
                    code=str(record['code'])
                    status_audit['error_counts'][code]=status_audit['error_counts'].get(code,0)+1
                if record.get('phase')==copy.PUBLISHED:status_audit['published_seen']=True
            return record
        def advance_copy():
            while not stop_worker.is_set():
                started=time.monotonic_ns()
                try:
                    result=peer_b.act(workspace_copy_status=copy.WorkspaceCopyStatusRequest(
                        protocol_version=1,operation_id=request.operation_id))
                    record=record_status(result,started)
                    if record['kind']!='action' or record['phase'] in (copy.ABORTED,copy.UNCERTAIN,copy.PUBLISHED):
                        return
                except BaseException as error:
                    with status_lock:
                        status_audit['exceptions'].append({'type':type(error).__name__,
                            'started_monotonic_ns':started,'returned_monotonic_ns':time.monotonic_ns()})
                    return
        worker=threading.Thread(target=advance_copy,name='copy-fault-status-poll',daemon=False)
        held=self.broker.audit_path.with_suffix('.seal-held')
        release=self.broker.audit_path.with_suffix('.seal-release')
        def persist_status_audit():
            # unittest records the original assertion before running cleanup callbacks.
            result=self._outcome.result
            audit_path=self.copy_evidence/'issuer-revocation-status.json'
            try:
                existing=json.loads(audit_path.read_text())
                existing_lifecycle=existing.get('worldd_lifecycle',{})
            except FileNotFoundError:
                existing_lifecycle={}
            except Exception as error:
                existing_lifecycle={}
                print('Revocation lifecycle merge read failed: '+type(error).__name__,file=sys.stderr)
            with status_lock:
                status_audit['worker_alive']=worker.is_alive()
                lifecycle=status_audit.setdefault('worldd_lifecycle',{})
                for key in ('before_world_close','after_world_close','stderr_sha256','stderr_lifecycle_tail'):
                    if key in existing_lifecycle:lifecycle[key]=existing_lifecycle[key]
                status_audit['test_failure_count']=sum(test is self for test,_ in result.failures)
                status_audit['test_error_count']=sum(test is self for test,_ in result.errors)
                text=json.dumps(status_audit,sort_keys=True,indent=2)+'\n'
            try:audit_path.write_text(text)
            except Exception as error:
                print('Revocation status diagnostic write failed: '+type(error).__name__,file=sys.stderr)
        def stop_copy_poll():
            stop_worker.set()
            if subprocess.run(['sudo','-n','test','-e',str(held)],capture_output=True).returncode==0:
                self.operator_run(['touch',release])
            if worker.is_alive():
                # Teardown interrupts this owned channel. It grants no second 35-second observation window.
                try:peer_b.stream.shutdown(socket.SHUT_RDWR)
                except OSError as error:
                    status_audit['cleanup_channel_shutdown_error']=type(error).__name__
                worker.join(timeout=5)
            if worker.is_alive():raise AssertionError('Copy status polling thread did not stop during cleanup')
        self.addCleanup(persist_status_audit)
        self.addCleanup(stop_copy_poll)
        worker.start()
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
        completion_deadline=release_sent+35_000_000_000
        status_audit['release_monotonic_ns']=release_sent
        status_audit['completion_deadline_monotonic_ns']=completion_deadline
        self.assertIsNotNone(witness,'Fresh issuer identity revocation did not complete')
        self.assertTrue(witness.HasField('identity_status'),witness)
        self.assertTrue(witness.identity_status.actor_revoked,witness)
        self.assertGreater(witness.identity_status.security_revision,0,witness)
        self.assertEqual(witness.identity_status.actor_id,actor_b.actor_id,witness)
        revocation_bytes=witness.SerializeToString()
        (self.copy_evidence/'issuer-revocation-receipt.pb').write_bytes(revocation_bytes)
        status_audit['revocation_receipt_sha256']=hashlib.sha256(revocation_bytes).hexdigest()
        status_audit['revoked_actor_id']=actor_b.actor_id.hex()
        status_audit['revocation_security_revision']=witness.identity_status.security_revision
        self.assertLess(release_sent-revoke_started,5_000_000_000,
            'Issuer revocation and release of the exact SEAL receipt exceeded the owner I/O budget')
        worker.join(timeout=max(0,(completion_deadline-time.monotonic_ns())/1e9))
        self.assertFalse(worker.is_alive(),'Status request did not return after the held receipt was forwarded')
        stop_worker.set()
        self.assertFalse(status_audit['exceptions'],'Actor status exchange raised instead of returning a typed denial')
        self.assertFalse(status_audit['published_seen'],'Revoked actor observed a published copy')
        # An issuer revocation can also invalidate the in-flight world session.
        # That fence replaces the message while preserving an underlying error code.
        last=status_audit['last']
        self.assertIsNotNone(last,'No actor status reply was recorded')
        if last['kind']!='error':
            # A redacted terminal journal status is not itself an actor-denial witness.
            remaining=(completion_deadline-time.monotonic_ns())/1e9
            self.assertGreater(remaining,0,'Revocation completion exceeded the single 35-second deadline')
            old_timeout=peer_b.stream.gettimeout()
            try:
                peer_b.stream.settimeout(min(5,remaining))
                started=time.monotonic_ns()
                reply=peer_b.act(workspace_copy_status=copy.WorkspaceCopyStatusRequest(
                    protocol_version=1,operation_id=request.operation_id))
                last=record_status(reply,started)
            finally:peer_b.stream.settimeout(old_timeout)
        self.assertEqual(last['kind'],'error','Revoked B request was not explicitly denied')
        self.assertTrue(is_revoked_copy_status_denial(last['code'],last['message'],
            unauthorized=core.UNAUTHORIZED,forbidden=core.FORBIDDEN),
            'Unexpected actor denial reason: '+repr((last['code'],last['message'])))
        status_audit['denial_validated']=True
        # The same release-based deadline covers durable abort observation, not 35+35 seconds.
        while time.monotonic_ns()<completion_deadline:
            terminal=read_copy_terminal_before_deadline(self.execution/'execution.db',request.operation_id,
                completion_deadline,status_audit['durable_busy'])
            self.assertIsNotNone(terminal,'Copy operation disappeared during durable abort observation')
            status_audit['durable_terminal']={'phase':terminal[0],'error':terminal[1],
                'destination_empty':terminal[2]=='','observed_monotonic_ns':time.monotonic_ns()}
            self.assertNotEqual(terminal[0],copy.PUBLISHED,'Revoked copy was durably published')
            if terminal[:2]==(copy.ABORTED,copy.COPY_FORBIDDEN):break
            time.sleep(min(.05,max(0,(completion_deadline-time.monotonic_ns())/1e9)))
        self.assertLessEqual(time.monotonic_ns(),completion_deadline,
                             'Durable forbidden abort exceeded the single 35-second deadline')
        self.assertEqual(terminal[:2],(copy.ABORTED,copy.COPY_FORBIDDEN))
        persist_status_audit()
        # Actor A remains authorized. Its successful launch proves the source barrier was removed.
        probe=self.launch_process(peer_a,"from pathlib import Path;Path('post-revoke-launch').write_text('ok')",
                                  operation='post-revocation-barrier-probe')
        self.wait_job(world,probe)
        for pid,start in payloads.items():self.assertEqual(self.start_time(pid),start,'Source PID/start identity changed')
        for pid in payloads:
            group=Path('/sys/fs/cgroup')/Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1].lstrip('/')
            self.assertIn('frozen 0',(group/'cgroup.events').read_text())
        with closing(sqlite3.connect(self.execution/'execution.db')) as database, database:
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


    def fault_action(self,peer,**operation):
        started=time.monotonic_ns()
        reply=peer.act(**operation)
        self.assertLess(time.monotonic_ns()-started,5_000_000_000,
                        'Actor exchange exceeded its unchanged five-second budget')
        return reply

    def restart_fault_world(self,world):
        old_broker=self.broker
        if hasattr(world,'retire_after_broker_death'):
            # The exact broker crash has already happened. Clean and retire its failed world afterward.
            self.broker.close()
            world.retire_after_broker_death(old_broker)
        world.close();self.broker.close()
        self.copy_fault_mode='pass-through'
        self.broker=CopyFaultStorageBrokerProcess(self,self.execution,self.authority_config)
        if hasattr(world,'retire_after_broker_death'):
            replacement=type(world)(self,world.original_config,world.state_path,
                runtime_config=world.runtime_path,broker=self.broker,previous=world)
        else:
            replacement=engine.fixtures.WorldProcess(self,world.config_path,world.state_path,
                runtime_config=world.runtime_path,broker=self.broker)
        self.worlds.append(replacement);replacement.management('system.hello')
        replacement.config_path=world.config_path;replacement.state_path=world.state_path
        replacement.runtime_path=world.runtime_path
        peer=replacement.peers[0]
        self.assertTrue(self.authenticate(peer).HasField('opened'))
        self.select(peer)
        return replacement,peer

    def copy_row(self,request):
        with closing(sqlite3.connect('file:'+str(self.execution/'execution.db')+'?mode=ro',uri=True,timeout=.5)) as database:
            row=database.execute('SELECT phase,error,request,authorization_binding FROM copy_operations WHERE operation_id=?',
                                 (request.operation_id,)).fetchone()
            effects=database.execute('SELECT kind,state,intent,receipt FROM copy_effects WHERE operation_id=? ORDER BY rowid',
                                     (request.operation_id,)).fetchall()
            count=database.execute('SELECT count(*) FROM workspaces').fetchone()[0]
        self.assertIsNotNone(row)
        self.assertEqual(row[2],request.SerializeToString())
        return row,effects,count

    def persist_fault_audit(self,broker,label):
        data=self.operator_run(['cat',broker.audit_path])
        (self.copy_evidence/(label+'-relay.json')).write_bytes(data)
        audit=json.loads(data)
        self.assertEqual(audit['mutations'],[])
        self.assertEqual(audit['cleanup_errors'],[])
        self.assertEqual(audit['owner_exit'],0,audit)
        self.assertEqual(audit['broker_exit'],0,audit)
        self.assertEqual(audit['forwarder_shutdown'],'owner-eof-before-forwarder-exit')
        return audit

    def assert_uncertain_retry(self,peer,request):
        reply=self.fault_action(peer,workspace_copy=request)
        self.assertTrue(reply.HasField('action'),reply)
        status=reply.action.copy_status
        self.assertEqual(status.operation_id,request.operation_id)
        self.assertEqual(status.phase,copy.UNCERTAIN,status)
        self.assertEqual(status.error_code,copy.COPY_UNCERTAIN,status)
        changed=copy.WorkspaceCopyRequest();changed.CopyFrom(request)
        changed.data_class[:]=list(reversed(changed.data_class))
        self.assertNotEqual(changed.SerializeToString(),request.SerializeToString())
        rejected=self.fault_action(peer,workspace_copy=changed)
        self.assertTrue(rejected.HasField('error'),rejected)
        self.assertEqual(rejected.error.code,core.OPERATION_CONFLICT,rejected)
        return status

    def test_lost_publication_ack_restart_reconciles_exact_receipt_without_duplicate_publish(self):
        self.copy_fault_mode='drop-publication-ack'
        expected_broker_executable=str(engine.fixtures.BROKER)
        with engine.fixtures.BROKER.open('rb') as executable_file:
            expected_broker_sha256=hashlib.file_digest(executable_file,'sha256').hexdigest()
        # Exercise operator rejection outside the held-receipt deadline with a safe test-owned process.
        probe=subprocess.Popen(['/usr/bin/sleep','60'])
        try:
            probe_pid=probe.pid;probe_start=self.start_time(probe_pid)
            probe_executable=os.readlink(f'/proc/{probe_pid}/exe')
            probe_sha256=hashlib.sha256(Path(probe_executable).read_bytes()).hexdigest()
            def reject_operator_identity(expected_pid=probe_pid,target_pid=probe_pid,
                                         expected_start=probe_start,expected_executable=probe_executable):
                return subprocess.run(['sudo','-n',sys.executable,'-B',str(SUPERVISED_BROKER_KILL),
                    '--expected-pid',str(expected_pid),'--target-pid',str(target_pid),
                    '--expected-start',str(expected_start),'--expected-executable',str(expected_executable),
                    '--expected-sha256',probe_sha256],capture_output=True,timeout=10)
            for expected_pid,target_pid,expected_start,expected_executable,reason in (
                    (probe_pid+1,probe_pid,probe_start,probe_executable,'target PID does not match'),
                    (probe_pid,probe_pid,str(int(probe_start)+1),probe_executable,'broker identity mismatch before pidfd'),
                    (probe_pid,probe_pid,probe_start,'/bin/sleep','broker identity mismatch before pidfd')):
                rejected=reject_operator_identity(expected_pid,target_pid,expected_start,expected_executable)
                self.assertNotEqual(rejected.returncode,0,rejected.stdout+rejected.stderr)
                self.assertIn(reason,rejected.stderr.decode(errors='replace'))
                self.assertIsNone(probe.poll(),'Rejected operator identity check killed the test-owned process')
                self.assertEqual(self.start_time(probe_pid),probe_start)
            accepted=reject_operator_identity()
            self.assertEqual(accepted.returncode,0,accepted.stdout+accepted.stderr)
            proof=json.loads(accepted.stdout)
            self.assertEqual(proof['pid'],probe_pid);self.assertEqual(proof['start_ticks'],probe_start)
            self.assertEqual(proof['executable'],probe_executable);self.assertEqual(proof['executable_sha256'],probe_sha256)
            self.assertLessEqual(proof['signal_sent_monotonic_ns'],proof['death_observed_monotonic_ns'])
            self.assertLessEqual(proof['death_observed_monotonic_ns']-proof['signal_sent_monotonic_ns'],5_000_000_000)
            self.assertEqual(probe.wait(timeout=5),-signal.SIGKILL)
        finally:
            if probe.poll() is None:
                probe.terminate();probe.wait(timeout=5)
        # Crash recovery needs a pre-bound real supervisor, not a fresh identity or a pre-crash close.
        sys.path.insert(0,str(engine.SOURCE/'tests/native'))
        from copy_fault_supervised_world import SupervisedCopyFaultWorld
        with patch.object(engine.fixtures.world_authority,'WorldProcess',SupervisedCopyFaultWorld):
            world=self.workspace(roots=lambda:self.copy_roots(True),registry=True)
        peer=world.peers[0];self.select(peer)
        request=self.request_copy(peer)
        accepted=self.fault_action(peer,workspace_copy=request)
        self.assertTrue(accepted.HasField('action'),accepted)
        self.assertEqual(accepted.action.copy_status.phase,copy.REQUESTED)
        old=self.broker
        held=old.audit_path.with_suffix('.publication-held')
        release=old.audit_path.with_suffix('.publication-release')
        self.addCleanup(lambda:self.operator_run(['touch',release]) if self.operator_directory.exists() else None)
        deadline=time.monotonic()+290
        while subprocess.run(['sudo','-n','test','-e',str(held)],capture_output=True).returncode:
            self.assertLess(time.monotonic(),deadline,'Copy never reached the real publication receipt')
            self.assertIsNone(old.proc.poll(),'Broker exited before the publication gate')
            time.sleep(.01)
        witnessed=json.loads(self.operator_run(['cat',held]))
        (self.copy_evidence/'lost-publication-held.json').write_text(json.dumps(witnessed,sort_keys=True,indent=2)+'\n')
        actual=storage.StorageOwnerReply.FromString(bytes.fromhex(witnessed['reply_hex']))
        publication=storage.StorageOwnerRequest.FromString(bytes.fromhex(witnessed['request_hex']))
        self.assertEqual(actual.copy_destination.state,storage.StorageCopyDestinationReceipt.PUBLISHED)
        row,effects,count=self.copy_row(request)
        self.assertEqual(row[0],copy.VALIDATED);self.assertEqual(count,1)
        self.assertEqual([(kind,state) for kind,state,_,_ in effects if kind=='publication'],[('publication',0)])
        binding_bytes=row[3]
        # The test actor cannot inspect this protected supervised broker.
        # The constrained operator helper revalidates its PID/start/ELF around pidfd open.
        identity=old.resources.handle.identity
        pid=old.pid;start=identity['start_ticks']
        executable=expected_broker_executable
        executable_sha256=expected_broker_sha256
        self.assertEqual(identity['pid'],pid);self.assertEqual(identity['executable'],executable)
        descriptor=os.pidfd_open(pid)
        try:
            self.assertEqual(select.select([descriptor],[],[],0)[0],[],'Broker exited before operator signal')
            killed=subprocess.run(['sudo','-n',sys.executable,'-B',str(SUPERVISED_BROKER_KILL),
                '--expected-pid',str(pid),'--target-pid',str(pid),'--expected-start',str(start),
                '--expected-executable',executable,'--expected-sha256',executable_sha256],
                capture_output=True,timeout=10)
            self.assertEqual(killed.returncode,0,killed.stdout+killed.stderr)
            proof=json.loads(killed.stdout)
            self.assertEqual(proof['pid'],pid);self.assertEqual(proof['start_ticks'],start)
            self.assertEqual(proof['executable'],executable);self.assertEqual(proof['executable_sha256'],executable_sha256)
            signal_sent=proof['signal_sent_monotonic_ns'];operator_death=proof['death_observed_monotonic_ns']
            self.assertLessEqual(signal_sent,operator_death)
            self.assertLessEqual(operator_death-signal_sent,5_000_000_000)
            self.assertTrue(select.select([descriptor],[],[],5)[0],
                            'Unprivileged broker pidfd did not confirm death before receipt release')
            death_observed=time.monotonic_ns()
            durable=(json.dumps(dict(broker_pid=pid,broker_start_ticks=start,**proof),sort_keys=True,indent=2)+'\n')
            (self.copy_evidence/'lost-publication-operator-kill-proof.json').write_text(durable)
            self.operator_run(['touch',release])
            receipt_released=time.monotonic_ns()
            (self.copy_evidence/'lost-publication-crash-order.json').write_text(json.dumps(dict(
                broker_pid=pid,broker_start_ticks=start,signal_sent_monotonic_ns=signal_sent,
                death_observed_monotonic_ns=death_observed,receipt_released_monotonic_ns=receipt_released),
                sort_keys=True,indent=2)+'\n')
            self.assertLessEqual(signal_sent,death_observed)
            self.assertLessEqual(death_observed,receipt_released)
        finally:
            os.close(descriptor)
            # Exceptional cleanup still releases the owned relay; it never establishes the normal-path oracle.
            self.operator_run(['touch',release])
        # This is the real supervisor wrapper exit; actual broker death was independently witnessed above.
        self.assertNotEqual(old.proc.wait(timeout=15),0)
        world,peer=self.restart_fault_world(world)
        audit=self.persist_fault_audit(old,'lost-publication-before-restart')
        self.assertEqual(audit['requests'].count('copy_destination_publish'),1,audit)
        self.assertFalse(audit['dropped_publication']['forwarded'])
        self.assertLess(audit['dropped_publication']['elapsed_seconds'],5)
        status=self.assert_uncertain_retry(peer,request)
        row,effects,count=self.copy_row(request)
        self.assertEqual(row[0],copy.UNCERTAIN);self.assertEqual(row[1],copy.COPY_UNCERTAIN)
        self.assertEqual(row[3],binding_bytes,'Restart or retry reset the pinned boot/deadline binding')
        self.assertEqual(count,1,'Uncertain owner publication must not create a registry workspace')
        publication_rows=[(state,receipt) for kind,state,_,receipt in effects if kind=='publication']
        self.assertEqual(len(publication_rows),1);self.assertEqual(publication_rows[0][0],1)
        exact_objects=b''.join(len(item.SerializeToString()).to_bytes(4,'big')+item.SerializeToString()
            for item in [*actual.copy_destination.objects,*actual.copy_destination.state_companions])
        self.assertEqual(publication_rows[0][1],exact_objects,'Recovery did not store the exact real owner object receipts')
        world.close();self.broker.close()
        after=self.persist_fault_audit(self.broker,'lost-publication-after-restart')
        self.assertIn('copy_destination_inspect',after['requests'],after)
        for method in ('copy_destination_prepare','copy_destination_publish','copy_stage_begin','copy_stage_start'):
            self.assertNotIn(method,after['requests'],after)
        inspect=storage.StorageCopyDestinationInspect(operation_id=request.operation_id,
            request_digest=publication.copy_destination_publish.allocation.request_digest)
        replies=self.stopped_owner_exchange([dict(copy_destination_inspect=inspect)],'lost-publication-exact-inspection')
        self.assertEqual(replies[0].copy_destination.SerializeToString(),actual.copy_destination.SerializeToString())
        (self.copy_evidence/'lost-publication-summary.json').write_text(json.dumps(dict(
            operation_id=request.operation_id.hex(),phase=status.phase,error=status.error_code,
            broker_pid=pid,broker_start_ticks=start,publication_receipt_sha256=hashlib.sha256(exact_objects).hexdigest(),
            unchanged_authorization_binding_sha256=hashlib.sha256(binding_bytes).hexdigest(),
            owner_publication_reply_sha256=witnessed['reply_sha256']),sort_keys=True,indent=2)+'\n')

    def test_hostile_git_hooks_filters_and_config_copy_sanitizes_admin_without_host_effects(self):
        self.copy_fault_mode='pass-through'
        world=self.workspace(roots=lambda:self.copy_roots(True),registry=True)
        peer=world.peers[0];self.select(peer)
        (self.project/'filter-input.txt').write_bytes(b'authorized filter bytes\n')
        (self.project/'.gitattributes').write_text('filter-input.txt filter=attack diff=attack\n')
        # Prepare the real index before introducing any executable configuration.
        self.git_oracle(self.project,'add','--','filter-input.txt','.gitattributes')
        marker=self.root/'hostile-git-host-effect'
        script=self.project/'hostile-git-command'
        script.write_text('#!/bin/sh\n/usr/bin/touch '+str(marker)+'\n/usr/bin/touch /project/hostile-git-executed\n/bin/cat\n')
        script.chmod(0o700)
        source_repository=self.execution/'repositories'/(self.repository_object+'.git')
        hooks=source_repository/'hooks';hooks.mkdir(exist_ok=True)
        for name in ('post-checkout','post-merge','pre-auto-gc','reference-transaction','fsmonitor-watchman'):
            hook=hooks/name;hook.write_bytes(script.read_bytes());hook.chmod(0o700)
        with (source_repository/'config').open('a') as configuration:
            configuration.write('\n[core]\n\thooksPath = '+str(hooks)+'\n\tfsmonitor = '+str(script)+
                '\n[filter "attack"]\n\tclean = '+str(script)+'\n\tsmudge = '+str(script)+
                '\n\trequired = true\n[diff "attack"]\n\ttextconv = '+str(script)+
                '\n[credential]\n\thelper = !'+str(script)+'\n')
        before=(source_repository/'config').read_bytes()
        self.assertFalse(marker.exists());self.assertFalse((self.project/'hostile-git-executed').exists())
        request=self.request_copy(peer)
        accepted=self.fault_action(peer,workspace_copy=request)
        self.assertTrue(accepted.HasField('action'),accepted)
        self.assertEqual(accepted.action.copy_status.phase,copy.REQUESTED)
        deadline=time.monotonic()+300
        while True:
            result=self.fault_action(peer,workspace_copy_status=copy.WorkspaceCopyStatusRequest(
                protocol_version=1,operation_id=request.operation_id))
            self.assertTrue(result.HasField('action'),result)
            status=result.action.copy_status
            self.assertNotIn(status.phase,(copy.ABORTED,copy.UNCERTAIN),status)
            if status.phase==copy.PUBLISHED:break
            self.assertLess(time.monotonic(),deadline,'Hostile Git control exceeded absolute operation budget')
            time.sleep(.01)
        self.assertEqual(status.phase,copy.PUBLISHED)
        with closing(sqlite3.connect(self.execution/'execution.db')) as database, database:
            repo,target=database.execute('SELECT r.path,t.path FROM workspaces w JOIN repositories r ON r.id=w.repo JOIN worktrees t ON t.id=w.worktree WHERE w.id=?',
                (status.authorized_destination_workspace_id,)).fetchone()
        target=Path(target);repo=Path(repo)
        self.assertEqual((source_repository/'config').read_bytes(),before)
        self.assertEqual((target/'filter-input.txt').read_bytes(),b'authorized filter bytes\n')
        self.assertEqual((target/'.gitattributes').read_bytes(),(self.project/'.gitattributes').read_bytes())
        self.assertFalse(marker.exists(),'Copy executed hostile Git configuration on the host')
        self.assertFalse((self.project/'hostile-git-executed').exists())
        self.assertFalse((target/'hostile-git-executed').exists(),'Copy executed hostile Git configuration in its sandbox')
        self.assertFalse((repo/'hooks').exists())
        config_bytes=(repo/'config').read_bytes()
        for token in (b'attack',b'filter',b'credential',b'fsmonitor',b'hooksPath',str(script).encode()):
            self.assertNotIn(token,config_bytes)
        self.assertFalse((repo/'objects/info/alternates').exists())
        self.assertTrue(all(item.stat().st_nlink==1 for item in repo.rglob('*') if item.is_file()))
        row,effects,count=self.copy_row(request)
        self.assertEqual(count,2);self.assertEqual(row[0],copy.PUBLISHED)
        self.assertEqual(len([1 for kind,state,_,_ in effects if kind=='publication' and state==1]),1)
        world.close();self.broker.close()
        audit=self.persist_fault_audit(self.broker,'hostile-git')
        self.assertEqual(audit['requests'].count('copy_destination_publish'),1)
        (self.copy_evidence/'hostile-git-summary.json').write_text(json.dumps(dict(
            phase=status.phase,source_config_sha256=hashlib.sha256(before).hexdigest(),
            destination_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
            host_effect=False,sandbox_effect=False),sort_keys=True,indent=2)+'\n')


    def test_real_copy_worker_death_restart_preserves_unresolved_output_without_relaunch(self):
        import process_scope_pb2 as scope
        self.copy_fault_mode='pass-through'
        world=self.workspace(roots=lambda:self.copy_roots(True),registry=True)
        peer=world.peers[0];self.select(peer)
        directory=self.project/'worker-death-input';directory.mkdir()
        block=b'actual production copy worker death regression\n'.ljust(4096,b'x')
        for number in range(3000):(directory/('%04d'%number)).write_bytes(block)
        owner_config=storage.StorageOwnerConfig.FromString(self.owner_configuration.read_bytes())
        storage_root=Path(owner_config.volume_root)
        self.assertEqual(storage_root.resolve(),self.execution.resolve())
        helper=Path('/usr/local/libexec/spatial-copy-tree-worker')
        helper_identity=helper.stat()
        self.assertEqual(helper_identity.st_uid,0)
        self.assertFalse(helper_identity.st_mode&0o022)
        request=self.request_copy(peer);stop=threading.Event();captured=[];errors=[]
        def interrupt_exact_worker():
            try:
                deadline=time.monotonic()+90
                while not stop.is_set() and time.monotonic()<deadline:
                    row,effects,_=self.copy_row(request) if (self.execution/'execution.db').exists() else (None,[],0)
                    launches=[receipt for kind,state,_,receipt in effects if kind=='worker-launch' and state==1]
                    stages=[receipt for kind,state,_,receipt in effects if kind=='stage-begin' and state==1]
                    if not launches or not stages:time.sleep(.001);continue
                    identity=scope.ProcessScopeState.FromString(launches[0]).identity
                    stage=storage.StorageCopyStageReceipt.FromString(stages[0])
                    self.assertEqual(stage.destination_object.kind,storage.StorageObject.COPY_STAGING)
                    staging=storage_root/'staging'/stage.destination_object.id
                    # Require copied bytes before interrupting the production helper.
                    progress=sum(item.stat().st_size for item in staging.rglob('*') if item.is_file())
                    if progress==0:time.sleep(.001);continue
                    for pid in self.descendants():
                        try:
                            executable=Path(f'/proc/{pid}/exe')
                            actual_executable=executable.stat()
                            if (actual_executable.st_dev,actual_executable.st_ino)!=(helper_identity.st_dev,helper_identity.st_ino):continue
                            descriptor=os.pidfd_open(pid)
                            try:
                                start=self.start_time(pid)
                                relative=Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1]
                                group=Path('/sys/fs/cgroup')/relative.lstrip('/')
                                owned=next((item for item in (group,*group.parents)
                                    if item.name==identity.scope_name),None)
                                if owned is None:continue
                                metadata=owned.stat()
                                if (metadata.st_dev,metadata.st_ino)!=(identity.device,identity.inode):continue
                                self.assertEqual(identity.boot_id,Path('/proc/sys/kernel/random/boot_id').read_text().strip())
                                self.assertEqual(len(identity.launch_id),32)
                                self.assertEqual(self.start_time(pid),start)
                                pinned_executable=executable.stat()
                                self.assertEqual((pinned_executable.st_dev,pinned_executable.st_ino),
                                                 (helper_identity.st_dev,helper_identity.st_ino))
                                self.assertEqual(Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1],relative)
                                # Pin first, then verify all identities, then signal that pidfd.
                                executable_link=os.readlink(executable)
                                signal.pidfd_send_signal(descriptor,signal.SIGKILL)
                                record=dict(worker_pid=pid,start_ticks=start,cgroup=str(group),
                                    scope_name=identity.scope_name,scope_device=identity.device,scope_inode=identity.inode,
                                    launch_id=identity.launch_id.hex(),boot_id=identity.boot_id,
                                    observed_staging_bytes=progress,signal='SIGKILL',
                                    executable_link=executable_link,
                                    executable_device=helper_identity.st_dev,executable_inode=helper_identity.st_ino,
                                    configured_storage_root=str(storage_root),
                                    worker_launch_receipt_sha256=hashlib.sha256(launches[0]).hexdigest(),
                                    stage_begin_receipt_sha256=hashlib.sha256(stages[0]).hexdigest())
                                captured.append(record)
                                (self.copy_evidence/'worker-death-identity.json').write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')
                                return
                            finally:os.close(descriptor)
                        except (FileNotFoundError,ProcessLookupError):continue
                    time.sleep(.001)
                if not stop.is_set():raise AssertionError('No active production worker matched its durable scope receipt before capture bound')
            except BaseException as error:errors.append(error)
        # Admit first, then sample. The owner thread advances the copy independently.
        accepted=self.fault_action(peer,workspace_copy=request)
        self.assertTrue(accepted.HasField('action'),accepted)
        self.assertEqual(accepted.action.copy_status.phase,copy.REQUESTED)
        worker=threading.Thread(target=interrupt_exact_worker,name='copy-worker-identity-interrupt',daemon=False)
        worker.start()
        def stop_monitor():
            stop.set();worker.join(timeout=5)
            if worker.is_alive():raise AssertionError('Owned worker monitor did not stop')
        self.addCleanup(stop_monitor)
        try:
            deadline=time.monotonic()+120
            while True:
                if errors:raise errors[0]
                result=self.fault_action(peer,workspace_copy_status=copy.WorkspaceCopyStatusRequest(
                    protocol_version=1,operation_id=request.operation_id))
                self.assertTrue(result.HasField('action'),result)
                status=result.action.copy_status
                if status.phase in (copy.UNCERTAIN,copy.ABORTED,copy.PUBLISHED):break
                self.assertLess(time.monotonic(),deadline,'Worker death did not reach bounded terminal status')
                time.sleep(.01)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            if errors:raise errors[0]
            self.assertEqual(len(captured),1,'Copy finished before actual worker interruption')
            self.assertEqual(status.phase,copy.UNCERTAIN,status)
            self.assertEqual(status.error_code,copy.COPY_UNCERTAIN,status)
        finally:stop_monitor()
        row,effects,count=self.copy_row(request)
        binding_bytes=row[3];self.assertEqual(count,1)
        self.assertEqual([(state,len(receipt)) for kind,state,_,receipt in effects if kind=='worker-output'],[(0,0)])
        self.assertFalse(any(kind in ('publication','registry-publication') for kind,_,_,_ in effects))
        stage_intent=next(intent for kind,state,intent,_ in effects if kind=='stage-begin' and state==1)
        stage=storage.StorageCopyStageBegin.FromString(stage_intent)
        old=self.broker
        world,peer=self.restart_fault_world(world)
        self.persist_fault_audit(old,'worker-death-before-restart')
        self.assert_uncertain_retry(peer,request)
        row,effects_after,count=self.copy_row(request)
        self.assertEqual(row[3],binding_bytes);self.assertEqual(count,1)
        self.assertEqual(effects_after,effects,'Restart or exact retry repeated or rewrote uncertain worker effects')
        world.close();self.broker.close()
        after=self.persist_fault_audit(self.broker,'worker-death-after-restart')
        self.assertFalse(set(after['requests'])&{'copy_stage_begin','copy_stage_start','copy_destination_prepare','copy_destination_publish'},after)
        inspect=storage.StorageCopyStageInspect(operation_id=stage.operation_id,request_digest=stage.request_digest)
        replies=self.stopped_owner_exchange([dict(copy_stage_inspect=inspect)],'dead-worker-exact-owner-inspect')
        receipt=replies[0].copy_stage
        self.assertEqual(receipt.state,storage.StorageCopyStageReceipt.UNCERTAIN)
        self.assertEqual(receipt.operation_id,stage.operation_id);self.assertEqual(receipt.request_digest,stage.request_digest)
        self.assertEqual(receipt.snapshot_object,stage.snapshot_object)
        self.assertEqual(receipt.destination_object,stage.destination_object)
        self.assertEqual(receipt.maximum_bytes,stage.maximum_bytes);self.assertEqual(receipt.maximum_entries,stage.maximum_entries)
        self.assertFalse(Path(captured[0]['cgroup']).exists(),'Owned worker cgroup survived checked cleanup')
        diagnostic=Path(old.log.name).read_bytes()
        self.assertIn(b'workspace copy failed operation='+request.operation_id.hex().encode(),diagnostic)
        (self.copy_evidence/'worker-death-broker.log').write_bytes(diagnostic)


def load_tests(loader,tests,pattern):
    names=('test_absolute_copy_budget_expires_across_real_subtimeout_owner_replies',
           'test_actor_identity_revocation_after_real_seal_receipt_blocks_copy_publication',
           'test_lost_publication_ack_restart_reconciles_exact_receipt_without_duplicate_publish',
           'test_hostile_git_hooks_filters_and_config_copy_sanitizes_admin_without_host_effects',
           'test_real_copy_worker_death_restart_preserves_unresolved_output_without_relaunch')
    suite=unittest.TestSuite(WorkspaceCopyFaultTests(name) for name in names)
    assert len(suite._tests)==5
    return suite

if __name__=='__main__':unittest.main()
