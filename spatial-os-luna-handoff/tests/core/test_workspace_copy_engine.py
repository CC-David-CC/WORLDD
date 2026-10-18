"""Actual broker begin with real owner, Git, grants, and resource boundaries.

Run on sb16/sb16-2. Runtime arguments match test_workspace_authority.py.
SPATIAL_TEST_BROKER_CONFIG must supply the delegated production resource template.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import stat
import sqlite3
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch
from google.protobuf import text_format
import broker_pb2 as broker
import cabinet_pb2 as cabinet
import file_actions_pb2 as file_actions
import core_pb2 as core
import copy_pb2 as copy
import grants_pb2 as grants
import issuer_pb2 as issuer
import storage_pb2 as storage
from harness.git_fixture import GitRepo
from harness.resource_broker import ResourceBroker
from harness.issuer_progress import IssuerLifetimeWitness
from harness.wire import frame
import test_workspace_authority as fixtures

BUILD=fixtures.BROKER.parent
SOURCE=Path(__file__).resolve().parents[2]

class StorageBrokerProcess(fixtures.BrokerProcess):
    def __init__(self,fixture,state,config=None):
        fixtures.initialize_broker_lifecycle(self,fixture,f'copy-broker-{len(fixture.brokers)}.log')
        self.fixture=fixture
        self.owner=None
        self.storage_log=None
        self._construction_sockets=[]
        child=owner_end=broker_end=None
        try:
            self.stream,child=socket.socketpair()
            self._construction_sockets.extend((self.stream,child))
            self.log=(fixture.root/f'copy-broker-{len(fixture.brokers)}.log').open('wb')
            self.owner_log=(fixture.root/f'copy-resource-{len(fixture.brokers)}.log').open('wb')
            self.storage_log=(fixture.root/f'copy-storage-{len(fixture.brokers)}.log').open('wb')
            owner_end,broker_end=socket.socketpair()
            self._construction_sockets.extend((owner_end,broker_end))
            for end in (owner_end,broker_end):end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
            command=[str(fixtures.BROKER),'--state-dir',str(state),'--storage-fd',str(broker_end.fileno())]
            if config:command+=['--authority-config',str(config)]
            configuration=str(fixture.copy_resource_config)
            self.resources=ResourceBroker(fixtures.BROKER,command,child,self.log,self.owner_log,configuration,
                                          extra_fds={'--storage-fd':broker_end.fileno()})
            self.proc=self.resources.process;self.pid=self.resources.pid
            broker_end.close();child.close()
            configuration=storage.StorageOwnerConfig.FromString(fixture.owner_configuration.read_bytes())
            configuration.broker_pid=self.pid
            local=fixture.root/f'owner-launch-{len(fixture.brokers)}.pb'
            local.write_bytes(configuration.SerializeToString());local.chmod(0o600)
            private=fixture.operator_directory/f'launch-{len(fixture.brokers)}.pb'
            fixture.operator_run(['install','-m','600',str(local),str(private)])
            code='import os,sys;os.dup2(0,3);os.set_inheritable(3,True);os.execv(sys.argv[1],sys.argv[1:])'
            owner_command=[str(BUILD/'spatial-storage-owner'),'--channel-fd','3','--config',str(private)]
            self.audit_path=None
            if getattr(fixture,'relay_mode',None) is not None:
                self.audit_path=fixture.operator_directory/f'relay-{len(fixture.brokers)}.json'
                owner_command=[sys.executable,'-B',str(SOURCE/'tests/native/storage_owner_receipt_relay.py'),
                    '--channel-fd','3','--build',str(BUILD),'--config',str(private),'--audit',str(self.audit_path)]
                if fixture.relay_mode:owner_command+=['--mutate']
                if getattr(fixture,'relay_delay',False):owner_command+=['--delay-copy-begin']
            self.owner=subprocess.Popen(['sudo','-n','env',
                'ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
                'UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
                sys.executable,'-B','-c',code,*owner_command],
                stdin=owner_end,stdout=subprocess.DEVNULL,stderr=self.storage_log)
            owner_end.close()
            self.initialize_transport(world_channel_opt_in=False)
            fixture.brokers.append(self)
        except BaseException as error:
            cleanup_errors=[]
            try:self.close()
            except BaseException as cleanup:cleanup_errors.append(cleanup)
            for endpoint in self._construction_sockets:
                try:endpoint.close()
                except BaseException as cleanup:cleanup_errors.append(cleanup)
            for cleanup in cleanup_errors:
                error.add_note('Partial storage broker cleanup also failed: '+repr(cleanup))
            raise
        finally:
            for endpoint in (child,owner_end,broker_end):
                if endpoint is not None:
                    try:endpoint.close()
                    except OSError:pass

    def close(self):
        errors=[]
        try:super().close()
        except BaseException as error:errors.append(error)
        owner=getattr(self,'owner',None)
        if owner is not None:
            try:
                result=owner.wait(timeout=10)
                if result!=0:errors.append(AssertionError('Actual storage owner exited '+str(result)))
            except BaseException as error:
                errors.append(error)
                if owner.poll() is None:
                    try:owner.terminate();owner.wait(timeout=5)
                    except BaseException as cleanup:
                        errors.append(cleanup)
                        if owner.poll() is None:
                            try:owner.kill();owner.wait(timeout=3)
                            except BaseException as final_cleanup:errors.append(final_cleanup)
        for endpoint in getattr(self,'_construction_sockets',()):
            try:endpoint.close()
            except BaseException as error:errors.append(error)
        storage_log=getattr(self,'storage_log',None)
        if storage_log is not None and not storage_log.closed:
            try:storage_log.close()
            except BaseException as error:errors.append(error)
        if errors:
            for error in errors[1:]:errors[0].add_note('Additional storage broker cleanup error: '+repr(error))
            raise errors[0]

class WorkspaceCopyEngineTests(IssuerLifetimeWitness,fixtures.WorkspaceAuthorityTests):
    # Fixture inputs are selected before the volume and owner policy are created.
    SOURCE_STATE_BYTES_BY_SCENARIO={
        'test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch':64*2**20,
        'test_optional_state_and_definition_copy_do_not_add_unselected_classes':64*2**20,
        'test_copied_policy_mapping_and_exact_publication_receipt_survive_reassignment_restart_and_recopy':64*2**20}
    # Continuous-writer positive scenarios declare capacity before owner/volume creation.
    SOURCE_POLICY_BYTES_BY_SCENARIO={
        'test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch':dict(
            actor_state_bytes=256*2**20,workspace_bytes=256*2**20,destination_copy_source_bytes=8*2**20),
        'test_optional_state_and_definition_copy_do_not_add_unselected_classes':dict(
            actor_state_bytes=256*2**20,workspace_bytes=256*2**20,destination_copy_source_bytes=4*2**20),
        'test_copied_policy_mapping_and_exact_publication_receipt_survive_reassignment_restart_and_recopy':dict(
            actor_state_bytes=256*2**20,workspace_bytes=256*2**20,destination_copy_source_bytes=4*2**20)}
    def operator_run(self,command):
        result=subprocess.run(['sudo','-n',*map(str,command)],capture_output=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result.stdout
    def setUp(self):
        super().setUp()
        self.assertIn(socket.gethostname().split('.')[0],('sb16','sb16-2'))
        self.assertNotEqual(os.getuid(),0,'Run fixture actor as dflanag3')
        self.name='m07-engine-'+uuid.uuid4().hex[:16]
        self.entry=Path('/var/lib/spatial/volumes')/self.name
        self.execution_override=self.entry/'mount'
        self.operator_directory=Path('/run')/self.name
        self.operator_run(['mkdir','-m','700',self.operator_directory])
        definition=broker.StorageVolumeDefinition()
        text_format.Parse((SOURCE/'config/operator-storage.textproto').read_text(),definition)
        definition.name=self.name
        policy=definition.owner_policy;policy.broker_uid=os.getuid()
        policy.volume_bytes=512*2**20;policy.control_plane_bytes=64*2**20;policy.filesystem_reserve_bytes=64*2**20
        source_policy=self.SOURCE_POLICY_BYTES_BY_SCENARIO.get(self._testMethodName,{})
        policy.default_policy.total_bytes=source_policy.get('workspace_bytes',64*2**20)
        policy.default_policy.actor_state_default_bytes=source_policy.get('actor_state_bytes',32*2**20)
        for profile in policy.default_policy.profiles:profile.state_bytes=4*2**20
        source_state_bytes=self.SOURCE_STATE_BYTES_BY_SCENARIO.get(self._testMethodName,4*2**20)
        for name in ('copy-source','copy-app'):
            policy.default_policy.profiles.add(name=name,state_bytes=source_state_bytes if name=='copy-source' else 4*2**20)
        self.copy_evidence=Path(os.environ['SPATIAL_SECURITY_EVIDENCE'])/self._testMethodName
        self.copy_evidence.mkdir(mode=0o700,parents=True,exist_ok=False)
        (self.copy_evidence/'volume.json').write_text(json.dumps(dict(name=self.name,entry=str(self.entry),volume=str(self.execution_override),
            operator_directory=str(self.operator_directory),source_state_ceiling_bytes=source_state_bytes,
            source_actor_ceiling_bytes=policy.default_policy.actor_state_default_bytes,
            source_workspace_ceiling_bytes=policy.default_policy.total_bytes,definition_hex=definition.SerializeToString(deterministic=True).hex(),qualification='UNQUALIFIED',cleanup='pending'),indent=2)+'\n')
        self.definition_path=self.root/'volume.textproto'
        self.definition_path.write_text(text_format.MessageToString(definition))
        self.owner_configuration=self.root/'owner.pb'
        self.addCleanup(self.cleanup_volume)
        created=json.loads(self.provision('create'))
        inventory=self.copy_evidence/'volume.json';record=json.loads(inventory.read_text());record['created']=created
        inventory.write_text(json.dumps(record,indent=2)+'\n')
        self.owner_configuration.write_bytes(self.operator_run(['cat',self.entry/'owner.pb']))
        self.owner_configuration.chmod(0o600)
        resource=broker.BrokerProcessConfig()
        text_format.Parse(Path(os.environ['SPATIAL_TEST_BROKER_CONFIG']).read_text(),resource)
        device=Path(subprocess.run(['findmnt','-n','-o','SOURCE','--target',str(self.execution_override)],
            capture_output=True,text=True,check=True).stdout.strip()).stat()
        major,minor=os.major(device.st_rdev),os.minor(device.st_rdev)
        for budget in (resource.resources.broker_budget,resource.resources.manager_budget,
                       resource.resources.default_policy.total,resource.resources.default_policy.actor_default,
                       *(profile.maximum for profile in resource.resources.default_policy.profiles)):
            if not any(item.major==major and item.minor==minor for item in budget.io):
                budget.io.add(major=major,minor=minor,read_bytes_per_second=64*2**20,write_bytes_per_second=16*2**20)
        for name in ('copy-source','copy-app'):
            application=resource.resources.default_policy.profiles.add(name=name)
            application.maximum.CopyFrom(resource.resources.default_policy.profiles[0].maximum)
            application.maximum.memory_bytes=64*2**20;application.maximum.processes=8;application.maximum.cpu_microseconds=25000
        copy_profile=resource.resources.copy_profiles.add(name='copy-limited',revision=1)
        copy_profile.policy.CopyFrom(resource.resources.default_policy)
        assigned=next(item for item in copy_profile.policy.profiles if item.name=='development')
        independent=type(assigned)();independent.CopyFrom(assigned)
        copy_profile.policy.ClearField('profiles');copy_profile.policy.profiles.add().CopyFrom(independent)
        copy_profile.policy.total.memory_bytes=256*2**20;copy_profile.policy.total.processes=32
        copy_profile.policy.actor_default.memory_bytes=128*2**20;copy_profile.policy.actor_default.processes=16
        copy_profile.policy.ClearField('actors')
        self.copy_resource_config=self.root/'copy-resources.textproto'
        self.copy_resource_config.write_text(text_format.MessageToString(resource));self.copy_resource_config.chmod(0o600)
        self.patcher=patch.object(fixtures,'BrokerProcess',StorageBrokerProcess)
        self.patcher.start();self.addCleanup(self.patcher.stop)
    def provision(self,operation):
        args=[sys.executable,'-B',SOURCE/'tools/provision_storage.py',operation,'--bindings-dir',BUILD/'python']
        args+=['--definition',self.definition_path] if operation=='create' else ['--name',self.name]
        return self.operator_run([*args,'--apply'])
    def retain_copy_journal(self):
        evidence=dict(operations=[],effects=[])
        if hasattr(self,'execution') and (self.execution/'execution.db').exists():
            with closing(sqlite3.connect('file:'+str(self.execution/'execution.db')+'?mode=ro',uri=True,timeout=1)) as db:
                for identity,phase,error,request,binding in db.execute('SELECT operation_id,phase,error,request,authorization_binding FROM copy_operations LIMIT 65'):
                    evidence['operations'].append(dict(operation_id=identity.hex(),phase=phase,error=error,
                        request_bytes=len(request),request_sha256=hashlib.sha256(request).hexdigest(),
                        binding_bytes=len(binding),binding_sha256=hashlib.sha256(binding).hexdigest()))
                for identity,kind,state,intent,receipt in db.execute('SELECT operation_id,kind,state,intent,receipt FROM copy_effects ORDER BY rowid LIMIT 1025'):
                    evidence['effects'].append(dict(operation_id=identity.hex(),kind=kind,state=state,intent_bytes=len(intent),
                        intent_sha256=hashlib.sha256(intent).hexdigest(),receipt_bytes=len(receipt),
                        receipt_sha256=hashlib.sha256(receipt).hexdigest()))
                self.assertLessEqual(len(evidence['operations']),64);self.assertLessEqual(len(evidence['effects']),1024)
        (self.copy_evidence/'journal.json').write_text(json.dumps(evidence,sort_keys=True,indent=2)+chr(10))
    def cleanup_volume(self):
        # Attempt every close. Capture durable evidence before the image is unmounted.
        failures=[]
        if hasattr(self,'lifecycle_processes'):
            self.retain_process_lifecycle('before-cleanup',self.lifecycle_owned,self.lifecycle_processes)
        try:self.capture_issuer_lifetime('before_copy_fixture_cleanup')
        except BaseException as error:failures.append('issuer lifetime capture: '+repr(error))
        for item in [*reversed(self.worlds),*reversed(self.brokers)]:
            try:item.close()
            except BaseException as error:failures.append(repr(error))
        if hasattr(self,'copy_evidence'):
            for index,broker in enumerate(getattr(self,'brokers',[])):
                audit_path=getattr(broker,'audit_path',None)
                try:
                    if audit_path is not None:
                        audit=self.operator_run(['cat',audit_path])
                        (self.copy_evidence/f'relay-audit-{index}.json').write_bytes(audit)
                except BaseException as error:failures.append('relay audit retention: '+repr(error))
        try:self.retain_copy_journal()
        except BaseException as error:failures.append('journal capture: '+repr(error))
        unmounted=False
        try:
            if self.entry.exists():self.provision('unmount')
            unmounted=True
        except BaseException as error:failures.append('volume cleanup: '+repr(error))
        if hasattr(self,'copy_evidence'):
            path=self.copy_evidence/'volume.json';record=json.loads(path.read_text())
            record['cleanup']='unmounted' if unmounted else 'unconfirmed';record['cleanup_failures']=failures
            path.write_text(json.dumps(record,indent=2)+chr(10))
        if not failures:self.operator_run(['rm','-r','--',self.operator_directory])
        self.assertFalse(failures,'Fixture cleanup failed: '+repr(failures))
    def after_workspace_storage_setup(self):
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            repo,project=db.execute('SELECT r.path,t.path FROM workspaces w JOIN repositories r ON r.id=w.repo JOIN worktrees t ON t.id=w.worktree WHERE w.id=?',('one',)).fetchone()
        self.repository_object=Path(repo).name.removesuffix('.git')
        self.project_object=Path(project).name
        self.assertEqual(len(self.project_object),32)
        # A filename grant matching this opaque storage ID must never read the root.
        (Path(project)/self.project_object).write_bytes(b'filename-shaped storage identity\n')
        config=storage.StorageOwnerConfig.FromString(self.owner_configuration.read_bytes())
        config.copy_catalog.protocol_version=1;config.copy_catalog.revision=1
        for data_class,kind,identity,schema in (
            (copy.PROJECT_TREE,storage.StorageObject.PROJECT,self.project_object,'project-tree'),
            (copy.GIT_HISTORY,storage.StorageObject.REPOSITORY,self.repository_object,'git-history')):
            item=config.copy_catalog.entries.add(workspace_id='one',data_class=data_class,
                schema_id='spatial.workspace.'+schema,schema_version=1,maximum_bytes=32*2**20,
                maximum_entries=10000,consistency=storage.CRASH_CONSISTENT_AFTER_QUIESCENCE)
            item.source_object.kind=kind;item.source_object.id=identity
        profile=config.copy_destination_profiles.add(name='copy-evaluation',revision=1,
            maximum_total_bytes=64*2**20,maximum_entries=20000,allowed_classes=[copy.PROJECT_TREE,copy.GIT_HISTORY],
            execution_resource_profile='copy-limited',application_profile='development',execution_policy=[core.WORKSPACE_SELECT,core.FILES_READ,core.FILES_WRITE,core.PROCESS_LAUNCH])
        profile.storage_policy.CopyFrom(config.default_policy);profile.storage_policy.total_bytes=32*2**20
        profile.storage_policy.actor_state_default_bytes=12*2**20
        # Preserve the pre-calibration destination policy, including its unused source profile.
        destination_source_bytes=self.SOURCE_POLICY_BYTES_BY_SCENARIO.get(self._testMethodName,{}).get('destination_copy_source_bytes')
        if destination_source_bytes is not None:
            next(item for item in profile.storage_policy.profiles if item.name=='copy-source').state_bytes=destination_source_bytes
        if hasattr(self,'configured_definitions'):
            config.copy_catalog.protocol_version=2
            self.selected_classes=[copy.PROJECT_TREE,copy.GIT_HISTORY,copy.APPLICATION_DEFINITION,copy.APPLICATION_STATE,copy.ENVIRONMENT_CONFIG]
            profile.allowed_classes[:]=self.selected_classes;profile.application_profile='copy-app'
            resources=broker.BrokerProcessConfig();text_format.Parse(self.copy_resource_config.read_text(),resources)
            selected=next(item for item in resources.resources.copy_profiles if item.name=='copy-limited')
            application=next(item for item in resources.resources.default_policy.profiles if item.name=='copy-app')
            independent=type(application)();independent.CopyFrom(application)
            selected.policy.ClearField('profiles');selected.policy.profiles.add().CopyFrom(independent)
            self.copy_resource_config.write_text(text_format.MessageToString(resources))
            state=storage.CopyApplicationStateCollection(schema_version=2)
            for application in self.configured_definitions.applications:
                slot=state.slots.add(key=application.key);slot.contract.CopyFrom(application.state_contract)
            environment=storage.CopyEnvironmentConfiguration(schema_version=1)
            dependency=environment.dependencies.add(name='python3',version=sys.version.split()[0],
                content_digest=hashlib.sha256(Path('/usr/bin/python3').read_bytes()).digest())
            for data_class,kind,schema,value in (
                (copy.APPLICATION_DEFINITION,storage.StorageObject.APPLICATION_DEFINITION,'application-definition',self.configured_definitions),
                (copy.APPLICATION_STATE,storage.StorageObject.APPLICATION_STATE,'application-state',state),
                (copy.ENVIRONMENT_CONFIG,storage.StorageObject.ENVIRONMENT_CONFIG,'environment-config',environment)):
                entry=config.copy_catalog.entries.add(workspace_id='one',data_class=data_class,schema_id='spatial.workspace.'+schema,
                    schema_version=1 if data_class==copy.ENVIRONMENT_CONFIG else 2,maximum_bytes=32*2**20,maximum_entries=10000,
                    consistency=storage.CRASH_CONSISTENT_AFTER_QUIESCENCE,typed_export=value.SerializeToString())
                entry.source_object.kind=kind;entry.source_object.id=uuid.uuid4().hex
        self.owner_configuration.write_bytes(config.SerializeToString())
        private=self.operator_directory/'migration.pb'
        self.operator_run(['install','-m','600',self.owner_configuration,private])
        self.operator_run([BUILD/'spatial-storage-owner','--config',private,'--migrate-copy-catalog'])
    def configure_state_writers(self,keys=('editor','terminal'),subdomains=(storage.PERSISTENT_DATA,storage.PERSISTENT_CONFIG)):
        self.configured_definitions=storage.CopyApplicationDefinitionCollection(schema_version=2)
        for key in keys:
            application=self.configured_definitions.applications.add(key=key,profile='copy-source')
            application.definition.schema_version=1;application.definition.name='Actual '+key+' state writer'
            application.definition.executable='/usr/bin/python3'
            application.state_contract.schema_id='spatial.application.quiesced-files';application.state_contract.schema_version=1
            application.state_contract.subdomains[:]=subdomains
            code=(
                'import os,time\nfrom pathlib import Path\n'
                'key='+repr(key)+'\n'
                'home=Path(os.environ["HOME"]);token=os.urandom(32).hex();assert not (home/"runtime-token").exists()\n'
                '(home/"runtime-token").write_text(token)\n'
                'Path(os.environ["XDG_CACHE_HOME"],"runtime-token").write_text(token)\n'
                'for domain,label in (("XDG_DATA_HOME","data"),("XDG_CONFIG_HOME","config")):\n'
                ' path=Path(os.environ[domain])/"counter";(home/("initial-"+label)).write_text(path.read_text() if path.exists() else "0")\n'
                'stop=Path("/project")/("stop-"+key)\n'
                'child=os.fork()\n'
                'domain="XDG_CONFIG_HOME" if child==0 else "XDG_DATA_HOME"\n'
                'root=Path(os.environ[domain]);root.mkdir(exist_ok=True)\n'
                'path=root/"counter";counter=int(path.read_text()) if path.exists() else 0\n'
                'while not stop.exists():\n'
                ' counter+=1;temporary=root/"counter.tmp";temporary.write_text(str(counter));temporary.replace(path);time.sleep(.005)\n'
                'if child!=0:os.waitpid(child,0)\n')
            application.definition.arguments[:]=['-c',code]
    def collection_roots(self):
        return self.copy_roots(True)+[self.workspace_terms(rights=[*fixtures.WRITE,grants.WORKSPACE_COPY_DEFINITION,
            grants.WORKSPACE_COPY_STATE,grants.WORKSPACE_COPY_ENVIRONMENT],depth=0)]
    def launch_configured(self,peer,key,operation):
        application=next(item for item in self.configured_definitions.applications if item.key==key)
        request=core.ProcessLaunchRequest(configured_application_key=key,profile=application.profile,operation_id=operation,
            argv=[application.definition.executable,*application.definition.arguments])
        reply=peer.act(process_launch=request);self.assertTrue(reply.HasField('action'),reply)
        return reply.action.process
    def configured_paths(self,workspace='one'):
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            rows=db.execute('SELECT key,state_id,contract FROM configured_application_states WHERE workspace=? ORDER BY key',(workspace,)).fetchall()
        return {key:(self.execution/'applications'/identity,storage.ApplicationStateContract.FromString(contract)) for key,identity,contract in rows}
    def wait_state_writes(self,paths,minimum=10):
        deadline=time.monotonic()+10
        while True:
            ready=True
            for root,contract in paths.values():
                for domain in contract.subdomains:
                    file=root/('data' if domain==storage.PERSISTENT_DATA else 'config')/'counter'
                    try:ready=ready and int(file.read_text())>=minimum
                    except (FileNotFoundError,ValueError):ready=False
            if ready:return
            self.assertLess(time.monotonic(),deadline,'Actual applications did not update declared persistent data/config')
            time.sleep(.02)
    def application_tree(self,root):
        return {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}
    def copy_to_completion(self,peer,request,owned,on_frozen=None):
        # Capture supervisor identities while live. Optional-state failure previously
        # lost its /proc evidence before teardown, although app stderr proved EDQUOT.
        label=request.operation_id.hex()
        processes=getattr(self,'lifecycle_processes',{})
        self.retain_process_lifecycle('copy-start-'+label,owned,processes)
        try:return self._copy_to_completion(peer,request,owned,on_frozen)
        except BaseException:
            self.retain_process_lifecycle('copy-failure-'+label,owned,processes)
            raise
    def _copy_to_completion(self,peer,request,owned,on_frozen=None):
        started=time.monotonic();reply=peer.act(workspace_copy=request)
        self.assertTrue(reply.HasField('action'),reply);self.assertEqual(reply.action.copy_status.phase,copy.REQUESTED,reply)
        self.assertLess(time.monotonic()-started,5)
        witnessed=False;deadline=started+300
        while True:
            payloads=[]
            for pid,start in owned.items():
                self.assertEqual(self.start_time(pid),start,'Source PID/start identity changed during copy')
                relative=Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1]
                group=Path('/sys/fs/cgroup')/relative.lstrip('/')
                if group.name=='payload':payloads.append((pid,'frozen 1' in (group/'cgroup.events').read_text()))
            if payloads and all(frozen for pid,frozen in payloads) and not witnessed:
                self.assertGreaterEqual(len(payloads),2);witnessed=True
                if on_frozen:on_frozen()
                self.assert_file_action_busy_without_effect(peer,'active')
                before=time.monotonic();self.select(peer);self.assertLess(time.monotonic()-before,5)
            reply=peer.act(workspace_copy_status=copy.WorkspaceCopyStatusRequest(protocol_version=1,operation_id=request.operation_id))
            self.assertTrue(reply.HasField('action'),reply);status=reply.action.copy_status
            self.assertNotIn(status.phase,(copy.ABORTED,copy.UNCERTAIN),status)
            if status.phase==copy.PUBLISHED:
                self.assertTrue(witnessed or not owned,'No coherent whole-payload quiescence witness');return status
            self.assertLess(time.monotonic(),deadline,'Actual copy exceeded its global operation budget');time.sleep(.01)
    def retain_process_lifecycle(self,label,owned,processes):
        try:self.write_process_lifecycle(label,owned,processes)
        except Exception as error:print('Process lifecycle diagnostic failed: '+repr(error),file=sys.stderr)
    def write_process_lifecycle(self,label,owned,processes):
        evidence=dict(label=label,monotonic=time.monotonic(),expected_starts=owned,
            applications={},broker_pid=self.broker.pid,processes=[],cgroups={},durable_processes=[],quiescence=[],
            state_witnesses=[],diagnostic_errors=[],diagnostic_unavailable=[])
        def bounded_file(path,maximum):
            try:
                descriptor=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
                try:
                    info=os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode):raise ValueError('Diagnostic file is not regular')
                    raw=os.read(descriptor,maximum+1)
                finally:os.close(descriptor)
                return dict(bytes=info.st_size,data_hex=raw[:maximum].hex(),truncated=len(raw)>maximum)
            except FileNotFoundError as error:
                evidence['diagnostic_unavailable'].append(str(path));return dict(unavailable=repr(error))
            except Exception as error:
                evidence['diagnostic_errors'].append(str(path)+': '+repr(error));return dict(error=repr(error))
        for key,value in processes.items():
            try:evidence['applications'][key]=value.SerializeToString(deterministic=True).hex()
            except Exception as error:evidence['diagnostic_errors'].append('application '+key+': '+repr(error))
        current=set()
        try:current=self.descendants()
        except Exception as error:evidence['diagnostic_errors'].append('descendants: '+repr(error))
        for pid in sorted(set(owned)|current):
            item=dict(pid=pid);evidence['processes'].append(item)
            for name in ('stat','status','cgroup','cmdline','task/'+str(pid)+'/children'):
                try:item[name]=(Path('/proc')/str(pid)/name).read_bytes().decode(errors='replace')
                except OSError as error:item[name+'_error']=repr(error)
            try:item['exe']=os.readlink('/proc/'+str(pid)+'/exe')
            except OSError as error:item['exe_error']=repr(error)
            try:
                relative=item['cgroup'].strip().split('::',1)[1]
                if not relative.startswith('/') or '..' in Path(relative).parts:raise ValueError('Invalid unified cgroup path')
                groups=[Path(relative)]
                if groups[0].name=='payload':groups.append(groups[0].parent)
                for group in groups:
                    key=str(group)
                    if key in evidence['cgroups']:continue
                    directory=Path('/sys/fs/cgroup')/key.lstrip('/')
                    evidence['cgroups'][key]={name:bounded_file(directory/name,16384) for name in
                        ('cgroup.events','cgroup.freeze','memory.events','memory.current','memory.max','memory.swap.max',
                         'pids.events','pids.current','pids.max','cpu.max','cpu.stat','io.stat')}
            except Exception as error:evidence['diagnostic_errors'].append('cgroup '+str(pid)+': '+repr(error))
        target=self.copy_evidence/('process-lifecycle-'+label+'.json')
        target.write_text(json.dumps(evidence,sort_keys=True,indent=2)+chr(10))
        try:
            with closing(sqlite3.connect('file:'+str(self.execution/'execution.db')+'?mode=ro',uri=True,timeout=1)) as db:
                for identity,state,payload,exit_code in db.execute('SELECT id,state,payload,exit_code FROM processes ORDER BY id'):
                    runtime=self.execution/'applications'/identity
                    evidence['durable_processes'].append(dict(id=identity,state=state,exit_code=exit_code,launch_request_hex=payload.hex(),
                        logs={name:bounded_file(runtime/name,65536) for name in ('stderr','stdout','setup.log')}))
                for identity,state,receipt in db.execute("SELECT operation_id,state,receipt FROM copy_effects WHERE kind='quiescence' ORDER BY operation_id,effect_id"):
                    evidence['quiescence'].append(dict(operation_id=identity.hex(),state=state,receipt_hex=receipt.hex()))
                for key,identity in db.execute("SELECT key,state_id FROM configured_application_states WHERE workspace='one' ORDER BY key"):
                    stop=self.project/('stop-'+key)
                    evidence['state_witnesses'].append(dict(key=key,state_id=identity,stop_exists=stop.exists(),
                        counters={name:bounded_file(self.execution/'applications'/identity/name/'counter',4096) for name in ('data','config')}))
        except Exception as error:evidence['diagnostic_errors'].append('database: '+repr(error))
        target.write_text(json.dumps(evidence,sort_keys=True,indent=2)+chr(10))
    def process_rows(self):
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            return db.execute('SELECT id,state FROM processes ORDER BY id').fetchall()
    def configured_request(self,key,operation):
        application=next(item for item in self.configured_definitions.applications if item.key==key)
        return core.ProcessLaunchRequest(configured_application_key=key,profile=application.profile,operation_id=operation,
            argv=[application.definition.executable,*application.definition.arguments])
    def assert_file_action_busy_without_effect(self,peer,label):
        name=('during-copy-'+label+'.txt').encode()
        request=cabinet.FileActionRequest(protocol_version=1,operation_id=uuid.uuid4().bytes,
            workspace_id='one',principal=self.principal(),
            create=file_actions.FileCreate(basename=name,content=b'forbidden'))
        reply=self.broker.request(file_action=request)
        self.assertTrue(reply.HasField('error'),reply)
        self.assertEqual(reply.error.code,core.BUSY,reply)
        self.assertFalse((self.project/name.decode()).exists())
    def assert_overlap_denied_without_effects(self,peer,key):
        before=self.process_rows();objects={path.name for path in (self.execution/'applications').iterdir()}
        reply=peer.act(process_launch=self.configured_request(key,'overlap-'+key))
        self.assertEqual(reply.error.code,core.BUSY,reply)
        self.assertEqual(self.process_rows(),before)
        self.assertEqual({path.name for path in (self.execution/'applications').iterdir()},objects)
    def assert_copy_has_no_authority_or_processes(self,destination):
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM processes WHERE workspace=?',(destination,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM workspace_members WHERE workspace=?',(destination,)).fetchone()[0],0)
            binding=broker.WorkspaceCopyResourceProfile.FromString(db.execute(
                'SELECT binding FROM workspace_copy_policies WHERE workspace=?',(destination,)).fetchone()[0])
            self.assertEqual(binding.name,'copy-limited')
            self.assertEqual([item.name for item in binding.policy.profiles],['copy-app'])
            self.assertEqual(binding.policy.total.memory_bytes,256*2**20)
    def test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch(self):
        self.configure_state_writers()
        world=self.workspace(roots=self.collection_roots,registry=True);peer=world.peers[0];self.select(peer)
        processes={key:self.launch_configured(peer,key,'first-'+key) for key in ('editor','terminal')}
        paths=self.configured_paths();self.assertEqual(set(paths),set(processes));self.wait_state_writes(paths)
        for key in processes:self.assert_overlap_denied_without_effects(peer,key)
        runtime_tokens={key:(self.execution/'applications'/process.process_id/'home'/'runtime-token').read_bytes()
                        for key,process in processes.items()}
        owned={pid:self.start_time(pid) for pid in self.descendants()};frozen={};frozen_inodes={}
        def capture():
            frozen.update({key:self.application_tree(root) for key,(root,contract) in paths.items()})
            self.assertTrue(all(frozen.values()))
            # Attempt1 reached PUBLISHED, then resumed writers renamed data/counter.tmp away.
            # Capture inode witnesses with the bytes while every source payload is frozen.
            frozen_inodes.update({key:{name:(paths[key][0]/name).stat().st_ino for name in files}
                                  for key,files in frozen.items()})
            self.assertEqual(set(frozen_inodes),set(frozen))
            for key in frozen:self.assertEqual(set(frozen_inodes[key]),set(frozen[key]))
            (self.copy_evidence/'frozen-source-file-identities.json').write_text(json.dumps(dict(
                monotonic=time.monotonic(),operation_id=request.operation_id.hex(),source_inodes=frozen_inodes,
                source_bytes_sha256={key:{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}
                                     for key,files in frozen.items()}),sort_keys=True,indent=2)+chr(10))
        request=self.request_copy(peer)
        self.lifecycle_processes=processes;self.lifecycle_owned=owned
        self.retain_process_lifecycle('before-copy',owned,processes)
        try:status=self.copy_to_completion(peer,request,owned,capture)
        except BaseException:
            self.retain_process_lifecycle('copy-failure',owned,processes)
            raise
        self.retain_process_lifecycle('copy-published',owned,processes)
        for pid,start in owned.items():self.assertEqual(self.start_time(pid),start,'Source PID/start changed at publication')
        destination=status.authorized_destination_workspace_id;self.assert_copy_has_no_authority_or_processes(destination)
        copied=self.configured_paths(destination);self.assertEqual(set(copied),set(paths))
        for key,(target,contract) in copied.items():
            self.assertNotEqual(target,paths[key][0]);self.assertEqual(contract,paths[key][1])
            actual=self.application_tree(target);self.assertEqual(actual,frozen[key])
            self.assertTrue(all(name.split('/')[0] in ('data','config') for name in actual))
            self.assertNotIn(runtime_tokens[key],actual.values())
            for name in actual:self.assertNotEqual((target/name).stat().st_ino,frozen_inodes[key][name])
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            binding=storage.WorkspaceCopyAuthorizationBinding.FromString(db.execute(
                'SELECT authorization_binding FROM copy_operations WHERE operation_id=?',(request.operation_id,)).fetchone()[0])
            recipe=storage.WorkspaceCopyRegistryPublication.FromString(db.execute(
                "SELECT intent FROM copy_effects WHERE operation_id=? AND kind='registry-publication'",(request.operation_id,)).fetchone()[0])
        self.assertGreater(binding.source_state_mapping_revision,0);self.assertEqual(len(binding.state_companions),2)
        self.assertEqual(recipe.owner_state_mapping_revision,1)
        self.assertEqual({item.key for item in recipe.states},set(paths))
        minimum=max(int(frozen[key][domain+'/counter']) for key in paths for domain in ('data','config'))+1
        self.wait_state_writes(paths,minimum=minimum)
        self.retain_process_lifecycle('source-progress-after-thaw',owned,processes)
        for key,process in processes.items():
            (self.project/('stop-'+key)).touch();self.wait_job(world,process)
        final_values={key:{domain:(root/domain/'counter').read_text() for domain in ('data','config')}
                      for key,(root,contract) in paths.items()}
        for key,process in processes.items():
            (self.project/('stop-'+key)).unlink()
            relaunched=self.launch_configured(peer,key,'second-'+key)
            self.assertNotEqual(relaunched.process_id,process.process_id)
            self.assertEqual(self.configured_paths()[key][0],paths[key][0])
            runtime=self.execution/'applications'/relaunched.process_id
            deadline=time.monotonic()+10
            # The token precedes initial state records in the actual payload startup sequence.
            initialization=[runtime/'home'/name for name in ('runtime-token','initial-data','initial-config')]
            while True:
                try:
                    ready=all(path.is_file() and path.stat().st_size>0 for path in initialization)
                    if ready:
                        with initialization[0].open('rb') as stream:token=stream.read(65)
                        ready=len(token)==64 and all(value in b'0123456789abcdef' for value in token)
                except FileNotFoundError:ready=False
                if ready:break
                self.assertLess(time.monotonic(),deadline);time.sleep(.01)
            self.assertNotEqual((runtime/'home'/'runtime-token').read_bytes(),runtime_tokens[key])
            for domain in ('data','config'):
                self.assertEqual((runtime/'home'/('initial-'+domain)).read_text(),final_values[key][domain])
            self.assert_overlap_denied_without_effects(peer,key)
            (self.project/('stop-'+key)).touch();self.wait_job(world,relaunched)
    def test_file_action_create_hits_real_workspace_qgroup_quota(self):
        self.workspace(attach_world=False)
        self.assertTrue((self.execution/'worktrees'/'tree-one').is_dir())
        maximum=64*2**20
        rejected=None
        for number in range(96):
            name=('quota-file-%03d.bin'%number).encode()
            request=cabinet.FileActionRequest(protocol_version=1,operation_id=uuid.uuid4().bytes,
                workspace_id='one',principal=self.principal(),
                create=file_actions.FileCreate(basename=name,content=os.urandom(1024*1024)))
            reply=self.broker.request(file_action=request)
            if reply.HasField('error'):
                self.fail('unexpected generic broker error during project qgroup test: %r'%reply.error)
            if reply.HasField('file_action') and reply.file_action.HasField('error'):
                rejected=reply
                action_error=reply.file_action.error
                self.assertIn(action_error.code,(file_actions.FileActionError.UNAVAILABLE,file_actions.FileActionError.UNCERTAIN),reply)
                residual=self.project/name.decode()
                if action_error.code==file_actions.FileActionError.UNCERTAIN:
                    self.assertEqual(action_error.recovery_operation_id,request.operation_id)
                    self.assertTrue(residual.exists())
                    self.assertTrue(residual.is_file())
                    self.assertGreaterEqual(residual.stat().st_size,0)
                    self.assertLessEqual(residual.stat().st_size,len(request.create.content))
                    with closing(sqlite3.connect(self.execution/'file-operations'/'file-operations.db')) as db:
                        row=db.execute('SELECT state FROM file_operations WHERE workspace=? AND operation=?',
                            ('one',request.operation_id)).fetchone()
                    self.assertEqual(row,(3,),'Possible filesystem effect lacks durable UNCERTAIN outcome')
                else:
                    self.assertFalse(residual.exists(),'Pre-effect qgroup denial created a file')
                break
            self.assertTrue(reply.HasField('file_action') and reply.file_action.HasField('object'),reply)
        self.assertIsNotNone(rejected,'The real workspace qgroup did not reject bounded 1 MiB file creates')
        subvolume=self.operator_run(['btrfs','subvolume','show',self.project]).decode()
        identity=next(int(line.split(':',1)[1].strip()) for line in subvolume.splitlines()
                      if line.strip().startswith('Subvolume ID:'))
        report=self.operator_run(['btrfs','qgroup','show','--raw',self.project]).decode()
        quota=next(line.split() for line in report.splitlines() if line.split() and line.split()[0]==f'0/{identity}')
        self.assertEqual(int(quota[3]),maximum,'Registered project qgroup limit differs from workspace policy')
        self.assertGreaterEqual(int(quota[1]),maximum-2*2**20,'Create rejection did not reach the actual project qgroup limit')
        self.broker.close()

    def test_small_source_quota_rejects_actual_writer_and_preserves_peer_application(self):
        self.configure_state_writers()
        probe=next(item for item in self.configured_definitions.applications if item.key=='editor')
        probe.definition.arguments[:]=['-c',
            'import errno,json,os\nfrom pathlib import Path\n'
            'path=Path(os.environ["XDG_DATA_HOME"])/"quota-probe";block=os.urandom(65536);written=0\n'
            'try:\n'
            ' with path.open("wb",buffering=0) as stream:\n'
            '  for index in range(128):\n'
            # A bounded POSIX write can accept a prefix before a later EDQUOT.
            # Account every accepted byte; retain the original rejection oracle.
            '   remainder=memoryview(block)\n'
            '   while remainder:\n'
            '    count=stream.write(remainder)\n'
            '    if count is None or count<=0 or count>len(remainder):raise RuntimeError("Invalid quota probe write progress")\n'
            '    written+=count;remainder=remainder[count:]\n'
            '   os.fsync(stream.fileno())\n'
            'except OSError as error:\n'
            ' if error.errno!=errno.EDQUOT:raise\n'
            ' print(json.dumps(dict(errno=error.errno,written_bytes=written,chunk_bytes=65536,attempt_bound_bytes=8388608)),flush=True)\n'
            ' raise SystemExit(73)\n'
            'raise RuntimeError("Quota probe completed 8MiB without EDQUOT")\n']
        world=self.workspace(roots=self.collection_roots,registry=True);peer=world.peers[0];self.select(peer)
        terminal=self.launch_configured(peer,'terminal','small-quota-peer')
        self.wait_state_writes(self.configured_paths())
        owned={pid:self.start_time(pid) for pid in self.descendants()}
        terminal_root=self.configured_paths()['terminal'][0]
        before={name:int((terminal_root/name/'counter').read_text()) for name in ('data','config')}
        editor=self.launch_configured(peer,'editor','small-quota-probe')
        self.lifecycle_processes=dict(editor=editor,terminal=terminal);self.lifecycle_owned=owned
        self.retain_process_lifecycle('quota-probe-started',owned,self.lifecycle_processes)
        deadline=time.monotonic()+20
        while True:
            with closing(sqlite3.connect('file:'+str(self.execution/'execution.db')+'?mode=ro',uri=True,timeout=1)) as db:
                result=db.execute('SELECT state,exit_code FROM processes WHERE id=?',(editor.process_id,)).fetchone()
            if result[0]=='exited':break
            self.assertEqual(result[0],'running',result)
            self.assertLess(time.monotonic(),deadline,'Bounded actual quota probe did not exit')
            self.select(peer);time.sleep(.01)
        self.assertEqual(result,('exited',73))
        self.assertEqual(world.management('process.wait',id=editor.process_id,timeout_seconds=0),{'exit_code':73})
        runtime=self.execution/'applications'/editor.process_id
        with (runtime/'stdout').open('rb') as stream:raw=stream.read(4097)
        self.assertLessEqual(len(raw),4096);rejection=json.loads(raw)
        self.assertEqual(rejection['errno'],122);self.assertEqual(rejection['chunk_bytes'],65536)
        self.assertEqual(rejection['attempt_bound_bytes'],8*2**20)
        self.assertGreater(rejection['written_bytes'],0);self.assertLess(rejection['written_bytes'],4*2**20)
        self.assertEqual((runtime/'stderr').read_bytes(),b'')
        paths=self.configured_paths();self.assertEqual(set(paths),{'editor','terminal'})
        self.assertLessEqual((paths['editor'][0]/'data/quota-probe').stat().st_size,4*2**20)
        self.wait_state_writes({'terminal':paths['terminal']},minimum=max(before.values())+1)
        for pid,start in owned.items():self.assertEqual(self.start_time(pid),start,'Peer PID/start changed after quota rejection')
        self.assertGreaterEqual(sum(Path(f'/proc/{pid}/cgroup').read_text().strip().endswith('/payload') for pid in owned),2)
        self.select(peer)
        self.retain_process_lifecycle('quota-probe-exited-peer-live',owned,self.lifecycle_processes)
        (self.project/'stop-terminal').touch();self.wait_job(world,terminal)
        world.close();self.broker.close()
        replies=self.stopped_owner_exchange([
            dict(configured_application_query=storage.StorageConfiguredApplicationQuery(workspace_id='one',key=key))
            for key in ('editor','terminal')]+[
            dict(inspect=storage.StorageInspect(object=storage.StorageObject(kind=storage.StorageObject.APPLICATION_STATE,id=process.process_id)))
            for process in (editor,terminal)],'small-quota-real-owner-inspection')
        states=[reply.configured_application.state for reply in replies[:2]]+[reply.state for reply in replies[2:]]
        self.assertEqual({item.maximum_bytes for item in states},{4*2**20})
        self.assertEqual(len({item.subvolume_id for item in states}),4)
        self.assertEqual(len({item.quota_group for item in states}),1);self.assertGreater(states[0].quota_group,0)
        for key,reply in zip(('editor','terminal'),replies):
            self.assertEqual(reply.configured_application.state.object.id,paths[key][0].name)
            self.assertEqual(reply.configured_application.storage_policy.actor_state_default_bytes,32*2**20)
            self.assertEqual(reply.configured_application.storage_policy.total_bytes,64*2**20)
        (self.copy_evidence/'small-quota-proof.json').write_text(json.dumps(dict(rejection=rejection,
            durable_probe_exit_code=73,peer_pid_starts=owned,peer_progress_after_probe_exit=True,
            owner_replies_hex=[reply.SerializeToString(deterministic=True).hex() for reply in replies]),sort_keys=True,indent=2)+chr(10))
    def test_optional_state_and_definition_copy_do_not_add_unselected_classes(self):
        self.configure_state_writers()
        world=self.workspace(roots=self.collection_roots,registry=True);peer=world.peers[0];self.select(peer)
        processes={key:self.launch_configured(peer,key,'optional-'+key) for key in ('editor','terminal')}
        self.wait_state_writes(self.configured_paths());owned={pid:self.start_time(pid) for pid in self.descendants()}
        for selected in (copy.APPLICATION_STATE,copy.APPLICATION_DEFINITION):
            request=self.request_copy(peer,[copy.PROJECT_TREE,copy.GIT_HISTORY,selected])
            status=self.copy_to_completion(peer,request,owned)
            with closing(sqlite3.connect(self.execution/'execution.db')) as db:
                binding=storage.WorkspaceCopyAuthorizationBinding.FromString(db.execute(
                    'SELECT authorization_binding FROM copy_operations WHERE operation_id=?',(request.operation_id,)).fetchone()[0])
            self.assertEqual({item.data_class for item in binding.owner_catalog.entries},{copy.PROJECT_TREE,copy.GIT_HISTORY,selected})
            self.assertEqual(len(binding.state_companions),2 if selected==copy.APPLICATION_STATE else 0)
            self.assertEqual(set(self.configured_paths(status.authorized_destination_workspace_id)),set(processes) if selected==copy.APPLICATION_STATE else set())
            self.assert_copy_has_no_authority_or_processes(status.authorized_destination_workspace_id)
        for key,process in processes.items():
            (self.project/('stop-'+key)).touch();self.wait_job(world,process)
    def stopped_owner_exchange(self,operations,label,expected_errors=None):
        self.assertTrue(all(item.proc.poll() is not None for item in self.brokers),'Private owner probe requires stopped brokers')
        configuration=storage.StorageOwnerConfig.FromString(self.owner_configuration.read_bytes())
        configuration.broker_pid=os.getpid();configuration.broker_uid=os.getuid()
        local=self.root/(label+'-owner.pb');local.write_bytes(configuration.SerializeToString());local.chmod(0o600)
        private=self.operator_directory/(label+'-owner.pb');self.operator_run(['install','-m','600',local,private])
        peer,child=socket.socketpair()
        for end in (peer,child):end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        peer.settimeout(10)
        log_path=self.copy_evidence/(label+'-owner.log')
        code='import os,sys;os.dup2(0,3);os.set_inheritable(3,True);os.execv(sys.argv[1],sys.argv[1:])'
        replies=[]
        with log_path.open('wb') as log:
            owner=subprocess.Popen(['sudo','-n',sys.executable,'-B','-c',code,str(BUILD/'spatial-storage-owner'),
                '--channel-fd','3','--config',str(private)],stdin=child,stdout=subprocess.DEVNULL,stderr=log)
            child.close()
            try:
                for number,operation in enumerate(operations,1):
                    request=storage.StorageOwnerRequest(protocol_version=1,request_id=number,**operation)
                    peer.sendall(frame(request));reply=fixtures.receive(peer.recv,peer,storage.StorageOwnerReply)
                    self.assertEqual(reply.protocol_version,1);self.assertEqual(reply.request_id,number)
                    if expected_errors and number in expected_errors:self.assertEqual(reply.error.code,expected_errors[number],reply)
                    else:self.assertFalse(reply.HasField('error'),reply)
                    replies.append(reply)
            finally:
                peer.shutdown(socket.SHUT_WR);peer.close()
                self.assertEqual(owner.wait(timeout=10),0,log_path.read_text())
        diagnostics=log_path.read_text()
        for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.assertNotIn(marker,diagnostics)
        return replies
    def assign_destination_authority(self,destination):
        directory=self.root/('authority-'+destination);directory.mkdir(mode=0o700)
        terms=self.workspace_terms(rights=[*fixtures.RIGHTS,grants.WORKSPACE_COPY_PROJECT,grants.WORKSPACE_COPY_GIT,
            grants.WORKSPACE_COPY_DEFINITION,grants.WORKSPACE_COPY_STATE,grants.WORKSPACE_COPY_ENVIRONMENT],depth=0)
        terms.scope.domain.id=destination
        seed=broker.WorkspaceAuthoritySeed(protocol_version=1,workspace=destination,authority_directory=str(directory),roots=[terms])
        path=self.root/('seed-'+destination+'.textproto');path.write_text(text_format.MessageToString(seed));path.chmod(0o600)
        result=subprocess.run([str(fixtures.BROKER),'--state-dir',str(self.execution),'--init-authority',str(path)],
            capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        config=broker.WorkspaceAuthorityConfig();text_format.Parse(self.authority_config.read_text(),config)
        # An expanded domain set must extend the stopped service, not register a
        # second participant that reclaims its existing workspace domains.
        self.assertTrue(all(world.proc.poll()==0 for world in self.worlds))
        self.assertEqual(self.broker.proc.returncode,0)
        self.assertTrue(self.broker.resources.closed)
        self.assertFalse(self.broker.resources.root.exists())
        runtime=issuer.AuthorityParticipantRuntimeConfig()
        text_format.Parse(Path(config.participant_runtime_config).read_text(),runtime)
        old_identity=tuple(sorted(grants.Domain(kind=grants.WORKSPACE,id=item.workspace).SerializeToString()
            for item in config.bindings))
        number,key,participant_id,revision=self.registered_services[old_identity]
        self.assertEqual(runtime.participant_id,participant_id)
        self.assertEqual(runtime.registered_revision,revision)
        self.assertEqual(runtime.private_key_path,str(key))
        with closing(self.connect()) as peer:
            op=issuer.IssuerOperation(registry_read=issuer.AuthorityRegistryRead(limit=16))
            reply=self.execute(peer,self.challenge(peer,op));self.assertTrue(reply.HasField('registry'),reply)
            page=reply.registry;self.assertFalse(page.next_after)
            matches=[item for item in page.participants if item.participant.id==participant_id]
            self.assertEqual(len(matches),1);registration=matches[0]
            self.assertEqual(registration.registered_revision,revision)
            self.assertEqual(tuple(sorted(domain.SerializeToString() for domain in registration.participant.domains)),old_identity)
            op=issuer.IssuerOperation(supervisor_read=issuer.AuthoritySupervisorRead(participant_id=participant_id))
            reply=self.execute(peer,self.challenge(peer,op))
            supervision=None
            if reply.HasField('supervision'):
                supervision=reply.supervision
                self.assertEqual(supervision.registration,registration)
            else:
                self.assertTrue(reply.HasField('error'),reply);self.assertEqual(reply.error.code,core.NOT_FOUND,reply)
            addition=issuer.AuthorityParticipantDomainsAdd(operation_id=uuid.uuid4().bytes,
                participant_id=participant_id,expected_public_key=registration.participant.public_key,
                expected_registered_revision=revision,expected_registry_revision=page.registry_revision,
                expected_supervision_revision=supervision.revision if supervision is not None else 0,
                additions=[grants.Domain(kind=grants.WORKSPACE,id=destination)])
            op=issuer.IssuerOperation(participant_domains_add=addition)
            reply=self.execute(peer,self.challenge(peer,op))
            self.assertTrue(reply.HasField('participant_domains_added'),reply);receipt=reply.participant_domains_added
        self.assertEqual(receipt.operation_id,addition.operation_id)
        self.assertEqual(receipt.request_bytes,addition.SerializeToString())
        self.assertEqual(receipt.request_sha256,hashlib.sha256(receipt.request_bytes).digest())
        self.assertEqual(receipt.old_registration,registration)
        self.assertEqual(receipt.registry_revision_before,page.registry_revision)
        self.assertEqual(receipt.registry_revision_after,page.registry_revision+1)
        self.assertEqual(receipt.new_registration.registered_revision,receipt.registry_revision_after)
        self.assertEqual(receipt.new_registration.issuer_id,registration.issuer_id)
        self.assertEqual(receipt.new_registration.participant.id,participant_id)
        self.assertEqual(receipt.new_registration.participant.public_key,registration.participant.public_key)
        new_identity=tuple(sorted((*old_identity,addition.additions[0].SerializeToString())))
        self.assertEqual(tuple(sorted(domain.SerializeToString()
            for domain in receipt.new_registration.participant.domains)),new_identity)
        if supervision is not None:
            self.assertEqual(receipt.old_supervision,supervision)
            self.assertEqual(receipt.new_supervision.registration,receipt.new_registration)
            self.assertEqual(receipt.new_supervision.public_key,supervision.public_key)
            self.assertEqual(receipt.new_supervision.revision,supervision.revision+1)
        else:
            self.assertFalse(receipt.HasField('old_supervision'));self.assertFalse(receipt.HasField('new_supervision'))
        evidence=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if evidence:
            target=Path(evidence)/('participant-domains-added-'+destination+'.textproto')
            target.write_text(text_format.MessageToString(receipt))
        # Retire the old cache key: its registration revision is now stale.
        del self.registered_services[old_identity]
        self.registry_revision=receipt.registry_revision_after
        self.registered_services[new_identity]=(number,key,participant_id,receipt.new_registration.registered_revision)
        config.bindings.add(workspace=destination,authority_directory=str(directory))
        self.configure_participant(config,[grants.Domain(kind=grants.WORKSPACE,id=item.workspace) for item in config.bindings])
        self.authority_config.write_text(text_format.MessageToString(config))
    def assert_destination_launch_guards(self,peer,stage):
        # Configured identity validation precedes resource-policy selection.
        # A valid raw development launch separately proves the copied policy denial.
        mismatch=self.configured_request('editor','configured-profile-mismatch-'+stage)
        mismatch.profile='copy-source'
        policy=core.ProcessLaunchRequest(argv=['/usr/bin/true'],profile='development',
            operation_id='excluded-development-profile-'+stage)
        for request,code,message in (
            (mismatch,core.INVALID_ARGUMENT,'Configured launch differs from its registered profile/executable/argv'),
            (policy,core.FORBIDDEN,'Application resource profile is unavailable')):
            before=self.process_rows()
            directories={path.name for path in (self.execution/'applications').iterdir()}
            result=peer.act(process_launch=request)
            self.assertTrue(result.HasField('error'),result)
            self.assertEqual(result.error.code,code,result)
            self.assertEqual(result.error.message,message,result)
            self.assertEqual(self.process_rows(),before)
            self.assertEqual({path.name for path in (self.execution/'applications').iterdir()},directories)
    def test_copied_policy_mapping_and_exact_publication_receipt_survive_reassignment_restart_and_recopy(self):
        self.configure_state_writers(keys=('editor',))
        world=self.workspace(roots=self.collection_roots,registry=True);peer=world.peers[0];self.select(peer)
        process=self.launch_configured(peer,'editor','restart-source')
        paths=self.configured_paths();self.wait_state_writes(paths)
        owned={pid:self.start_time(pid) for pid in self.descendants()}
        request=self.request_copy(peer);status=self.copy_to_completion(peer,request,owned)
        destination=status.authorized_destination_workspace_id;self.assert_copy_has_no_authority_or_processes(destination)
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            allocation=storage.StorageCopyDestinationPrepare.FromString(db.execute(
                "SELECT intent FROM copy_effects WHERE operation_id=? AND kind='destination-allocation'",(request.operation_id,)).fetchone()[0])
            policy_bytes=db.execute('SELECT binding FROM workspace_copy_policies WHERE workspace=?',(destination,)).fetchone()[0]
            validation=db.execute("SELECT receipt FROM copy_effects WHERE operation_id=? AND kind='destination-validation'",(request.operation_id,)).fetchone()[0]
            publication=storage.StorageCopyDestinationPublish(allocation=allocation);publication.validation.ParseFromString(validation)
        (self.project/'stop-editor').touch();self.wait_job(world,process);world.close();self.broker.close()
        inspect=storage.StorageCopyDestinationInspect(operation_id=allocation.operation_id,request_digest=allocation.request_digest)
        original=self.stopped_owner_exchange([dict(copy_destination_inspect=inspect),dict(copy_destination_prepare=allocation),
            dict(copy_destination_publish=publication)],'before-reassignment')
        serialized=original[0].copy_destination.SerializeToString()
        self.assertEqual(original[1].copy_destination.SerializeToString(),serialized)
        self.assertEqual(original[2].copy_destination.SerializeToString(),serialized)
        self.assertEqual(original[0].copy_destination.destination_state_mapping_revision,1)
        self.assign_destination_authority(destination)
        self.broker=StorageBrokerProcess(self,self.execution,self.authority_config)
        world=self.scoped_world(fixtures.session.REGISTERED_ACTORS,broker=self.broker)
        world.management('workspace.available',id=destination,available=True);peer=world.peers[0]
        selected=peer.act(workspace_select=core.WorkspaceIdRequest(id=destination));self.assertTrue(selected.HasField('action'),selected)
        self.assert_destination_launch_guards(peer,'first-restart')
        allowed=self.configured_request('editor','destination-configured');allowed.profile='copy-app'
        result=peer.act(process_launch=allowed);self.assertTrue(result.HasField('action'),result);dest_process=result.action.process
        copied=self.configured_paths(destination);self.wait_state_writes(copied)
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            target=Path(db.execute('SELECT t.path FROM workspaces w JOIN worktrees t ON t.id=w.worktree WHERE w.id=?',(destination,)).fetchone()[0])
            self.assertEqual(db.execute('SELECT binding FROM workspace_copy_policies WHERE workspace=?',(destination,)).fetchone()[0],policy_bytes)
        (target/'stop-editor').touch();self.wait_job(world,dest_process);world.close();self.broker.close()
        source=self.stopped_owner_exchange([dict(configured_application_query=
            storage.StorageConfiguredApplicationQuery(workspace_id='one',key='editor'))],'source-before-quota-transfer')[0].configured_application
        state_maximum=min(source.storage_policy.total_bytes,source.storage_policy.actor_state_default_bytes,
            next(item.state_bytes for item in source.storage_policy.profiles if item.name==source.definition.profile))
        transfer=storage.StorageConfiguredApplicationPrepare(persistent=storage.StorageApplicationPrepare(workspace='one',
            object=source.state.object,fixture_actor='receipt-source-reassignment',profile=source.definition.profile,
            configured_application_key='editor',expected_catalog_revision=source.catalog_revision,
            expected_state_mapping_revision=source.state_mapping_revision,expected_state_maximum_bytes=state_maximum,
            expected_storage_policy_digest=hashlib.sha256(source.storage_policy.SerializeToString()).digest()),
            runtime_object=storage.StorageObject(kind=storage.StorageObject.APPLICATION_STATE,id=uuid.uuid4().hex))
        changed=storage.StorageCopyDestinationPrepare();changed.CopyFrom(allocation);changed.profile_revision+=1
        wrong_publication=storage.StorageCopyDestinationPublish();wrong_publication.CopyFrom(publication);wrong_publication.validation.head='0'*40
        after=self.stopped_owner_exchange([dict(configured_application_prepare=transfer),
            dict(copy_destination_inspect=inspect),dict(copy_destination_prepare=allocation),dict(copy_destination_publish=publication),
            dict(configured_application_query=storage.StorageConfiguredApplicationQuery(workspace_id=destination,key='editor')),
            dict(copy_destination_prepare=changed),dict(copy_destination_publish=wrong_publication)],
            'after-source-and-destination-reassignment-restart',expected_errors={6:storage.StorageOwnerError.CONFLICT,7:storage.StorageOwnerError.CONFLICT})
        self.assertGreater(after[0].configured_allocation.state_mapping_revision,source.state_mapping_revision)
        for reply in after[1:4]:self.assertEqual(reply.copy_destination.SerializeToString(),serialized)
        self.assertGreater(after[4].configured_application.state_mapping_revision,1)
        restarted=self.stopped_owner_exchange([dict(copy_destination_inspect=inspect),dict(copy_destination_prepare=allocation),
            dict(copy_destination_publish=publication)],'publication-after-owner-restart')
        for reply in restarted:self.assertEqual(reply.copy_destination.SerializeToString(),serialized)
        self.broker=StorageBrokerProcess(self,self.execution,self.authority_config)
        world=self.scoped_world(fixtures.session.REGISTERED_ACTORS,broker=self.broker)
        world.management('workspace.available',id=destination,available=True);peer=world.peers[0]
        result=peer.act(workspace_select=core.WorkspaceIdRequest(id=destination));self.assertTrue(result.HasField('action'),result)
        self.assert_destination_launch_guards(peer,'second-restart')
        page=peer.call(grants=fixtures.session.WorldGrantList(workspace=destination));self.assertTrue(page.HasField('grants'),page)
        recopy=copy.WorkspaceCopyRequest(protocol_version=1,operation_id=uuid.uuid4().bytes,source_workspace_id=destination,
            expected_source_authority_revision=page.grants.revision,expected_source_policy_revision=1,
            destination_policy_profile='copy-evaluation',expected_destination_profile_revision=1,data_class=self.selected_classes)
        fresh=self.copy_to_completion(peer,recopy,{})
        self.assertNotIn(fresh.authorized_destination_workspace_id,('one',destination))
        self.assert_copy_has_no_authority_or_processes(fresh.authorized_destination_workspace_id)
        recopied=self.configured_paths(fresh.authorized_destination_workspace_id)
        self.assertEqual(set(recopied),{'editor'})
        self.assertEqual(self.application_tree(recopied['editor'][0]),self.application_tree(copied['editor'][0]))
    def test_owner_rejects_raw_runtime_state_schema1_before_effects(self):
        self.configure_state_writers(keys=('editor',))
        world=self.workspace(roots=self.collection_roots,registry=True);peer=world.peers[0];self.select(peer)
        process=self.launch_configured(peer,'editor','state1-source');self.wait_state_writes(self.configured_paths())
        (self.project/'stop-editor').touch();self.wait_job(world,process);world.close();self.broker.close()
        configuration=storage.StorageOwnerConfig.FromString(self.owner_configuration.read_bytes())
        entry=next(item for item in configuration.copy_catalog.entries if item.data_class==copy.APPLICATION_STATE)
        entry.schema_version=1;entry.ClearField('typed_export');entry.source_object.id=process.process_id
        configuration.copy_catalog.revision+=1;configuration.broker_pid=os.getpid()
        local=self.root/'unsafe-state1.pb';local.write_bytes(configuration.SerializeToString());local.chmod(0o600)
        private=self.operator_directory/'unsafe-state1.pb';self.operator_run(['install','-m','600',local,private])
        database=self.execution/'.storage-owner/catalog.db'
        before=self.operator_run(['sha256sum',database])
        objects={path.name for path in (self.execution/'applications').iterdir()}
        peer,child=socket.socketpair()
        for end in (peer,child):end.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
        code='import os,sys;os.dup2(0,3);os.set_inheritable(3,True);os.execv(sys.argv[1],sys.argv[1:])'
        try:
            result=subprocess.run(['sudo','-n',sys.executable,'-B','-c',code,str(BUILD/'spatial-storage-owner'),
                '--channel-fd','3','--config',str(private)],stdin=child,capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn(b'Raw application-state schema1 is unsafe',result.stderr)
            (self.copy_evidence/'state1-admission-refusal.log').write_bytes(result.stderr)
        finally:peer.close();child.close()
        self.assertEqual(self.operator_run(['sha256sum',database]),before)
        self.assertEqual({path.name for path in (self.execution/'applications').iterdir()},objects)
    def test_data_only_contract_keeps_config_fresh_across_instance_relaunch(self):
        self.configure_state_writers(keys=('editor',),subdomains=(storage.PERSISTENT_DATA,))
        world=self.workspace(roots=self.collection_roots,registry=True);peer=world.peers[0];self.select(peer)
        first=self.launch_configured(peer,'editor','data-only-first');paths=self.configured_paths();self.wait_state_writes(paths)
        self.assertFalse((paths['editor'][0]/'config').exists())
        (self.project/'stop-editor').touch();self.wait_job(world,first)
        data=(paths['editor'][0]/'data'/'counter').read_text()
        old_runtime=self.execution/'applications'/first.process_id
        self.assertGreater(int((old_runtime/'config'/'counter').read_text()),0)
        (self.project/'stop-editor').unlink()
        second=self.launch_configured(peer,'editor','data-only-second')
        runtime=self.execution/'applications'/second.process_id;deadline=time.monotonic()+10
        while not (runtime/'home'/'initial-config').exists():
            self.assertLess(time.monotonic(),deadline);time.sleep(.01)
        self.assertEqual((runtime/'home'/'initial-data').read_text(),data)
        self.assertEqual((runtime/'home'/'initial-config').read_text(),'0')
        self.assertFalse((paths['editor'][0]/'config').exists())
        (self.project/'stop-editor').touch();self.wait_job(world,second)
    def copy_roots(self,root_read):
        rights=[grants.WORKSPACE_ACCESS,grants.PROCESS_EXECUTE,grants.POLICY_ADMINISTER,
                grants.FILE_BROWSE,grants.WORKSPACE_COPY_PROJECT,grants.WORKSPACE_COPY_GIT]
        roots=[self.workspace_terms(rights=rights,depth=0)]
        roots.append(self.workspace_terms(rights=[grants.FILE_READ],depth=0,
                                         components=() if root_read else (self.project_object.encode('utf-8'),)))
        return roots
    def request_copy(self,peer=None,classes=None):
        page=self.own(peer) if peer else self.broker.request(grants=broker.GrantListRequest(workspace='one',principal=self.principal())).grants
        return copy.WorkspaceCopyRequest(protocol_version=1,operation_id=uuid.uuid4().bytes,source_workspace_id='one',
            expected_source_authority_revision=page.revision,expected_source_policy_revision=1,
            destination_policy_profile='copy-evaluation',expected_destination_profile_revision=1,
            data_class=classes or getattr(self,'selected_classes',[copy.PROJECT_TREE,copy.GIT_HISTORY]))
    def test_begin_rejects_storage_id_filename_read_before_requested(self):
        self.workspace(roots=lambda:self.copy_roots(False),registry=True,attach_world=False)
        request=self.request_copy()
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            before=db.execute('SELECT count(*) FROM copy_operations').fetchone()[0]
        reply=self.broker.request(workspace_copy_begin=broker.WorkspaceCopyBeginRequest(principal=self.principal(),request=request))
        self.assertEqual(reply.error.code,core.FORBIDDEN,reply)
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM copy_operations').fetchone()[0],before)
        self.assertFalse(list((self.execution/'snapshots').iterdir()),'Denied begin allocated a snapshot')
        self.assertFalse(list((self.execution/'staging').iterdir()),'Denied begin allocated staging')
    def test_begin_root_read_reaches_durable_requested_and_exact_retry(self):
        self.workspace(roots=lambda:self.copy_roots(True),registry=True,attach_world=False)
        request=self.request_copy();start=time.monotonic()
        begin=broker.WorkspaceCopyBeginRequest(principal=self.principal(),request=request)
        reply=self.broker.request(workspace_copy_begin=begin)
        self.assertLess(time.monotonic()-start,5)
        self.assertEqual(reply.workspace_copy_status.phase,copy.REQUESTED,reply)
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            row=db.execute('SELECT request,requester,authorization_binding FROM copy_operations WHERE operation_id=?',(request.operation_id,)).fetchone()
        self.assertEqual(row[0],request.SerializeToString());self.assertEqual(row[1],self.principal().issuer_id+self.principal().actor_id)
        binding=storage.WorkspaceCopyAuthorizationBinding.FromString(row[2])
        self.assertEqual(binding.owner_catalog.catalog_revision,1)
        self.assertEqual(binding.exact_request_digest,hashlib.sha256(row[0]).digest())
        retried=self.broker.request(workspace_copy_begin=begin)
        self.assertEqual(retried.workspace_copy_status.operation_id,request.operation_id,retried)
        changed=broker.WorkspaceCopyBeginRequest();changed.CopyFrom(begin);changed.request.destination_policy_profile='different'
        rejected=self.broker.request(workspace_copy_begin=changed)
        self.assertEqual(rejected.error.code,core.OPERATION_CONFLICT,rejected)

    def git_oracle(self,path,*arguments):
        result=subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-C',str(path),*arguments],
            capture_output=True,timeout=15,env=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',
                GIT_AUTHOR_NAME='Copy Witness',GIT_AUTHOR_EMAIL='copy@example.invalid',
                GIT_COMMITTER_NAME='Copy Witness',GIT_COMMITTER_EMAIL='copy@example.invalid'))
        self.assertEqual(result.returncode,0,result.stderr);return result.stdout
    @staticmethod
    def start_time(pid):
        value=Path(f'/proc/{pid}/stat').read_text();return value[value.rfind(')')+2:].split()[19]
    def test_actor_copy_publishes_independent_dirty_git_and_preserves_source_process(self):
        self.copy_actor_git_state()
    def test_copy_broker_wait_keeps_action_issuer_alive_beyond_tls_idle(self):
        self.relay_delay=True
        self.copy_actor_git_state()
        self.assertTrue(self.copy_status_timings)
        self.assertLess(max(self.copy_status_timings),10)
        (self.copy_evidence/'owner-delay-rpc-timing.json').write_text(json.dumps(dict(
            actor_status_elapsed_seconds=self.copy_status_timings,broker_exchange_budget_seconds=10,
            issuer_io_budget_seconds=5,owner_io_budget_seconds=5,
            operation_300_second_deadline_coverage='UNRUN; this is a successful4.1s nested progress control'),indent=2)+chr(10))
        observed=self.capture_issuer_lifetime('after_real_4_1_second_owner_reply_delay')
        self.assertEqual(observed['policy']['idle_timeout_ms'],3000)
        self.assertEqual(observed['policy']['io_timeout_ms'],5000)
        self.assertTrue(observed['processes'])
        self.assertTrue(all(item['exit_code'] is None and item['start_ticks'] for item in observed['processes']))
        self.worlds[-1].close();self.broker.close()
        audit=json.loads(self.operator_run(['cat',self.broker.audit_path]))
        self.assertEqual(len(audit['delays']),1,audit)
        delay=audit['delays'][0]
        self.assertEqual(delay['method'],'copy_stage_begin')
        self.assertGreaterEqual(delay['elapsed_seconds'],4.1)
        self.assertLess(delay['elapsed_seconds'],5)
        # A real status reply must complete while the owner effect is still pending.
        delay_start_ns=int(delay['start_monotonic']*1_000_000_000)
        delay_end_ns=int(delay['end_monotonic']*1_000_000_000)
        during_wait=[item for item in self.copy_status_timing_records
                     if item['started_monotonic_ns']>=delay_start_ns and
                        item['ended_monotonic_ns']<=delay_end_ns and item['error'] is None]
        self.assertTrue(during_wait,'No actor status response completed inside the pending owner exchange')
        self.assertEqual(audit['mutations'],[])
        self.assertEqual(audit['owner_exit'],0)
        self.assertEqual(audit['broker_exit'],0)
    def test_actor_copy_preserves_confined_checkout_and_commit(self):
        self.copy_actor_git_state(changed_head=True)
    def test_actor_copy_preserves_confined_detached_commit(self):
        self.copy_actor_git_state(changed_head=True,detached=True)
    def copy_actor_git_state(self,changed_head=False,detached=False):
        self.copy_status_timings=[]
        self.copy_status_timing_records=[]
        self.relay_mode=False
        def roots():
            result=self.copy_roots(True)
            if changed_head:result.append(self.workspace_terms(rights=fixtures.WRITE,depth=0))
            return result
        world=self.workspace(roots=roots,registry=True)
        peer=world.peers[0];self.select(peer)
        if changed_head:
            code=('import os,subprocess;from pathlib import Path;'
                  'os.environ.update(GIT_AUTHOR_NAME="Confined Copy",GIT_AUTHOR_EMAIL="copy@example.invalid",'
                  'GIT_COMMITTER_NAME="Confined Copy",GIT_COMMITTER_EMAIL="copy@example.invalid");'
                  'subprocess.run(["git","checkout","-b","copy-current"],check=True);'
                  'Path("README.txt").write_text("committed by actual confined application");'
                  'subprocess.run(["git","commit","-am","confined current commit"],check=True);')
            if detached:
                code=code.replace('subprocess.run(["git","commit"', 'subprocess.run(["git","checkout","--detach","HEAD"],check=True);subprocess.run(["git","commit"')
            self.wait_job(world,self.launch_process(peer,code,operation='copy-change-head'))
            with closing(sqlite3.connect(self.execution/'execution.db')) as db:
                old_branch,old_head=db.execute('SELECT t.branch,t.head FROM workspaces w JOIN worktrees t ON t.id=w.worktree WHERE w.id=?',('one',)).fetchone()
            self.assertNotEqual(self.git_oracle(self.project,'rev-parse','HEAD').decode().strip(),old_head)
            self.assertNotEqual(self.git_oracle(self.project,'rev-parse','--abbrev-ref','HEAD').decode().strip(),old_branch)
        process=self.launch_process(peer,'import time;time.sleep(360)',operation='copy-source-continuity')
        owned={pid:self.start_time(pid) for pid in self.descendants()}
        self.assertTrue(owned)
        witness={}
        for pid,start in owned.items():
            status=Path(f'/proc/{pid}/status').read_text()
            ns=[int(value) for line in status.splitlines() if line.startswith('NSpid:') for value in line.split()[1:]]
            relative=Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1]
            witness[str(pid)]=dict(start_ticks=start,namespace_pids=ns,pid_namespace_inode=Path(f'/proc/{pid}/ns/pid').stat().st_ino,
                cgroup=relative,argv=Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(bytes([0])).decode().split(chr(0)))
        payloads=[value for value in witness.values() if Path(value['cgroup']).name=='payload']
        self.assertEqual(len(payloads),1,witness)
        self.assertEqual(payloads[0]['argv'],['/usr/bin/python3','-c','import time;time.sleep(360)'])
        self.assertGreaterEqual(payloads[0]['namespace_pids'][-1],2)
        (self.copy_evidence/'source-processes.json').write_text(json.dumps(witness,sort_keys=True,indent=2)+chr(10))
        (self.project/'README.txt').write_bytes(b'staged version\n')
        self.git_oracle(self.project,'add','--','README.txt')
        (self.project/'README.txt').write_bytes(b'unstaged version\n')
        (self.project/'untracked.bin').write_bytes(b'actual untracked project bytes\x00\xff')
        (self.project/'.env').write_bytes(b'arbitrary authorized project bytes stay unchanged\n')
        before={name:self.git_oracle(self.project,*args) for name,args in {
            'staged':('diff','--cached','--binary'),'unstaged':('diff','--binary'),
            'status':('status','--porcelain=v1','-z'),'history':('rev-list','--all'),
            'head':('rev-parse','HEAD'),'branch':('rev-parse','--abbrev-ref','HEAD')}.items()}
        source_repository=self.execution/'repositories'/(self.repository_object+'.git')
        canary=b'fixture-credential-canary-excluded-from-git-administration'
        (source_repository/'undeclared-admin-sidecar').write_bytes(canary)
        (source_repository/'logs').mkdir(exist_ok=True)
        (source_repository/'logs'/'HEAD').write_bytes(canary+b'\n')
        self.git_oracle(self.project,'config','--local','remote.origin.url','https://fixture.invalid/'+canary.decode())
        request=self.request_copy(peer);started=time.monotonic()
        result=peer.act(workspace_copy=request)
        self.assertTrue(result.HasField('action'),result)
        self.assertEqual(result.action.copy_status.phase,copy.REQUESTED,result)
        self.assertLess(time.monotonic()-started,5)
        deadline=time.monotonic()+300;observed_frozen_access=False
        while True:
            for pid,start in owned.items():self.assertEqual(self.start_time(pid),start,'Source PID/start identity changed during copy')
            frozen=[]
            for pid in owned:
                relative=Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1]
                group=Path('/sys/fs/cgroup')/relative.lstrip('/')
                if group.name=='payload' and 'frozen 1' in (group/'cgroup.events').read_text():frozen.append(pid)
            if frozen and not observed_frozen_access:
                started_access=time.monotonic()
                self.select(peer)
                self.assertLess(time.monotonic()-started_access,5)
                observed_frozen_access=True
            status_started=time.monotonic()
            status_started_ns=time.monotonic_ns()
            status_error=None
            try:
                result=peer.act(workspace_copy_status=copy.WorkspaceCopyStatusRequest(protocol_version=1,operation_id=request.operation_id))
            except BaseException as error:
                status_error=repr(error)
                raise
            finally:
                status_ended_ns=time.monotonic_ns()
                elapsed=time.monotonic()-status_started
                self.copy_status_timings.append(elapsed)
                self.copy_status_timing_records.append(dict(
                    attempt=len(self.copy_status_timing_records)+1,
                    started_monotonic_ns=status_started_ns,
                    ended_monotonic_ns=status_ended_ns,
                    elapsed_ns=status_ended_ns-status_started_ns,
                    elapsed_seconds=elapsed,
                    error=status_error))
                (self.copy_evidence/'actor-status-timings.json').write_text(
                    json.dumps(self.copy_status_timing_records,sort_keys=True,indent=2)+chr(10))
            self.assertTrue(result.HasField('action'),result)
            status=result.action.copy_status
            self.assertNotIn(status.phase,(copy.ABORTED,copy.UNCERTAIN),status)
            if status.phase==copy.PUBLISHED:break
            self.assertLess(time.monotonic(),deadline,'Actual copy did not publish within its operation budget')
            time.sleep(.02)
        self.assertTrue(observed_frozen_access,'No access/status witness observed the real frozen source payload')
        destination=status.authorized_destination_workspace_id;self.assertNotEqual(destination,'one')
        self.assertEqual(len(status.authorized_manifest_digest),32)
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            destination_repo,destination_project=db.execute('SELECT r.path,t.path FROM workspaces w JOIN repositories r ON r.id=w.repo JOIN worktrees t ON t.id=w.worktree WHERE w.id=?',(destination,)).fetchone()
            self.assertEqual(db.execute('SELECT count(*) FROM processes WHERE workspace=?',(destination,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM workspace_members WHERE workspace=?',(destination,)).fetchone()[0],0)
            binding_bytes=db.execute('SELECT binding FROM workspace_copy_policies WHERE workspace=?',(destination,)).fetchone()[0]
            binding=broker.WorkspaceCopyResourceProfile.FromString(binding_bytes)
            self.assertEqual(binding.name,'copy-limited');self.assertEqual(binding.policy.total.memory_bytes,256*2**20)
            self.assertEqual([profile.name for profile in binding.policy.profiles],['development'])
        target=Path(destination_project)
        for name,args in {'staged':('diff','--cached','--binary'),'unstaged':('diff','--binary'),
                          'status':('status','--porcelain=v1','-z'),'history':('rev-list','--all'),
                          'head':('rev-parse','HEAD'),'branch':('rev-parse','--abbrev-ref','HEAD')}.items():
            self.assertEqual(self.git_oracle(target,*args),before[name],name)
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            branch=db.execute('SELECT t.branch FROM workspaces w JOIN worktrees t ON t.id=w.worktree WHERE w.id=?',(destination,)).fetchone()[0]
        self.assertEqual(branch,'' if detached else before['branch'].decode().strip())
        self.assertEqual((target/'untracked.bin').read_bytes(),(self.project/'untracked.bin').read_bytes())
        self.assertEqual((target/'.env').read_bytes(),(self.project/'.env').read_bytes())
        self.assertNotIn(str(self.execution/'repositories'/self.repository_object),(target/'.git').read_text())
        self.assertIn(destination_repo,(target/'.git').read_text())
        for entry in Path(destination_repo).rglob('*'):
            if entry.is_file():self.assertEqual(entry.stat().st_nlink,1,str(entry))
        self.assertFalse((Path(destination_repo)/'objects/info/alternates').exists())
        self.assertFalse((Path(destination_repo)/'undeclared-admin-sidecar').exists())
        self.assertFalse((Path(destination_repo)/'logs').exists())
        self.assertNotIn(canary,(Path(destination_repo)/'config').read_bytes())
        for pid,start in owned.items():self.assertEqual(self.start_time(pid),start,'Source PID/start identity changed')
        page=self.own(peer);base=next(item for item in page.grants if grants.WORKSPACE_COPY_PROJECT in item.terms.rights)
        self.revoke_grant(peer,base)
        redacted=peer.act(workspace_copy_status=copy.WorkspaceCopyStatusRequest(protocol_version=1,operation_id=request.operation_id))
        self.assertTrue(redacted.HasField('action'),redacted)
        self.assertFalse(redacted.action.copy_status.HasField('authorized_destination_workspace_id'))
        self.assertFalse(redacted.action.copy_status.HasField('authorized_manifest_digest'))

    def test_async_actor_copy_rejects_authenticated_malformed_begin_before_start(self):
        self.relay_mode=True
        world=self.workspace(roots=lambda:self.copy_roots(True),registry=True)
        request=self.request_copy(world.peers[0])
        reply=world.peers[0].act(workspace_copy=request)
        self.assertTrue(reply.HasField('action'),reply)
        self.assertEqual(reply.action.copy_status.phase,copy.REQUESTED,reply)
        self.assertNotEqual(self.broker.proc.wait(timeout=30),0,'Malformed owner receipt did not fence the actual broker')
        world.close();self.broker.close()
        audit=json.loads(self.operator_run(['cat',self.broker.audit_path]))
        self.assertEqual(len(audit['mutations']),1,audit)
        self.assertIn('copy_stage_begin',audit['requests'])
        self.assertNotIn('copy_stage_start',audit['requests'])
        self.assertNotIn('copy_stage_seal',audit['requests'])
        self.assertNotIn('copy_destination_publish',audit['requests'])
        with closing(sqlite3.connect(self.execution/'execution.db')) as db:
            self.assertEqual(db.execute('SELECT phase FROM copy_operations WHERE operation_id=?',(request.operation_id,)).fetchone()[0],copy.UNCERTAIN)
            self.assertEqual(db.execute("SELECT count(*) FROM copy_effects WHERE operation_id=? AND kind='worker-launch'",(request.operation_id,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM workspaces').fetchone()[0],1)
        self.assertTrue(all(not any(directory.iterdir()) for directory in (self.execution/'staging').iterdir()),'Malformed BEGIN copied bytes before its exact root binding was validated')

def load_tests(loader,tests,pattern):
    return unittest.TestSuite(WorkspaceCopyEngineTests(name) for name in (
        'test_begin_rejects_storage_id_filename_read_before_requested',
        'test_begin_root_read_reaches_durable_requested_and_exact_retry',
        'test_actor_copy_publishes_independent_dirty_git_and_preserves_source_process',
        'test_copy_broker_wait_keeps_action_issuer_alive_beyond_tls_idle',
        'test_async_actor_copy_rejects_authenticated_malformed_begin_before_start',
        'test_actor_copy_preserves_confined_checkout_and_commit',
        'test_actor_copy_preserves_confined_detached_commit',
        'test_two_live_apps_all_five_classes_copy_latest_declared_state_and_relaunch',
        'test_small_source_quota_rejects_actual_writer_and_preserves_peer_application',
        'test_optional_state_and_definition_copy_do_not_add_unselected_classes',
        'test_data_only_contract_keeps_config_fresh_across_instance_relaunch',
        'test_owner_rejects_raw_runtime_state_schema1_before_effects',
        'test_copied_policy_mapping_and_exact_publication_receipt_survive_reassignment_restart_and_recopy'))

if __name__=='__main__':unittest.main()
